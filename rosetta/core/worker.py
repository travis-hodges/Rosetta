"""The long-lived M worker process and its supervisor.

`docker exec` + `mumps -run` costs ~448ms. Forking one per case makes the
benchmark impossible, so exactly one `mumps` process is kept alive behind a
pipe and every case is a request/response round trip on that pipe.

The worker can die -- VistA error traps and RPC/menu routines HALT routinely,
and a bare `KILL` in the code under test wipes the worker's own symbol table.
Death is a normal outcome here, not an exception: `send()` raises
`WorkerDied`, the caller records ``error="HALT"``, and `ensure_started()`
brings a fresh process back.
"""

from __future__ import annotations

import logging
import os
import select
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .config import DEFAULT_CONFIG, M_WORKER_SOURCE, CoreConfig
from .protocol import Request, Response

log = logging.getLogger("rosetta.core.worker")

_BANNER = "ROSETTA-WORKER"


class WorkerError(RuntimeError):
    """The worker is reachable but could not serve the request."""


class WorkerTimeout(WorkerError):
    """The worker was alive but did not answer inside the deadline.

    Distinct from WorkerDied: section 9 treats a bare timeout as a weak
    signal that must be flagged separately, so the two cannot be conflated.
    """


class WorkerDied(WorkerError):    """The worker process exited or timed out mid-request."""


class MWorker:
    """One supervised `mumps -run MAIN^ROSWRK` process."""

    def __init__(self, config: CoreConfig | None = None) -> None:
        self.config = config or DEFAULT_CONFIG
        self._proc: subprocess.Popen[bytes] | None = None
        self._buf = b""
        self._stderr: list[str] = []
        self._stderr_thread: threading.Thread | None = None
        self.spawn_count = 0

    # ------------------------------------------------------------- lifecycle

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def ensure_started(self) -> None:
        if self.alive:
            return
        self.stop()
        self._install_worker_source()
        self._spawn()

    def _docker(self, *args: str) -> list[str]:
        return [self.config.docker, *args]

    def _check_container(self) -> None:
        cfg = self.config
        if shutil.which(cfg.docker) is None:
            raise WorkerError(f"{cfg.docker!r} not found on PATH")
        proc = subprocess.run(
            self._docker("inspect", "-f", "{{.State.Status}}", cfg.container),
            capture_output=True,
            text=True,
        )
        state = proc.stdout.strip()
        if proc.returncode != 0 or state != "running":
            raise WorkerError(
                f"container {cfg.container!r} is not running (state={state or 'absent'}). "
                f"Run scripts/bootstrap.sh first."
            )

    def _install_worker_source(self) -> None:
        """Copy ROSWRK.m into the container. Cheap and idempotent."""
        cfg = self.config
        self._check_container()
        if not M_WORKER_SOURCE.is_file():
            raise WorkerError(f"missing M worker source at {M_WORKER_SOURCE}")
        # Everything lands in THIS runtime's private sandbox. Writing the
        # worker into the container's shared routine directory raced other
        # verifiers: deleting a shared ROSWRK.o while another process ZLINKed
        # it produced %YDB-E-INVOBJFILE.
        _run(self._docker("exec", "-u", cfg.instance, cfg.container,
                          "mkdir", "-p", cfg.scratch_dir, cfg.snapshot_dir,
                          cfg.private_src, cfg.private_obj))
        dest = f"{cfg.private_src}/ROSWRK.m"
        _run(self._docker("cp", str(M_WORKER_SOURCE), f"{cfg.container}:{dest}"))
        _run(self._docker("exec", "-u", "root", cfg.container,
                          "chown", "-R", f"{cfg.instance}:{cfg.instance}", cfg.private_dir))
        # Private object dir, so a recompile cannot shadow or be shadowed.
        _run(self._docker("exec", "-u", cfg.instance, cfg.container, "bash", "-c",
                          f"rm -f {cfg.private_obj}/ROSWRK.o"), check=False)

    def _spawn(self) -> None:
        cfg = self.config
        cmd = self._docker(
            "exec", "-i", "-u", cfg.instance, cfg.container,
            "bash", "-c",
            f"source {cfg.env_file} && cd {cfg.scratch_dir} && "
            # Prepend this runtime's private (object, source) pair so ZLINK
            # compiles into a directory nothing else writes. The shared dirs
            # stay on the path, read-only, so real VistA routines still resolve.
            f'export gtmroutines="{cfg.private_obj}*({cfg.private_src}) $gtmroutines" && '
            f'export ROSWRKOBJD="{cfg.private_obj}" && '
            f"exec $gtm_dist/mumps -run MAIN^ROSWRK",
        )
        log.debug("spawning worker: %s", " ".join(cmd))
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self.spawn_count += 1
        self._buf = b""
        self._stderr = []
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, args=(self._proc,), daemon=True
        )
        self._stderr_thread.start()

        banner = self._read_line(self.config.startup_timeout_s)
        if banner is None or not banner.startswith(_BANNER):
            detail = "\n".join(self._stderr[-20:])
            self.stop()
            raise WorkerDied(
                f"worker did not announce itself (got {banner!r}). stderr:\n{detail}"
            )

    def _drain_stderr(self, proc: subprocess.Popen[bytes]) -> None:
        stream = proc.stderr
        if stream is None:
            return
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line:
                self._stderr.append(line)
                log.debug("worker stderr: %s", line)

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin is not None:
                try:
                    proc.stdin.write(b"CMD\tHALT\nEND\n")
                    proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        finally:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass

    def __enter__(self) -> "MWorker":
        self.ensure_started()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    # --------------------------------------------------------------- request

    def send(self, request: Request, timeout_s: float | None = None) -> Response:
        """One round trip. Raises WorkerDied on exit or timeout."""
        self.ensure_started()
        proc = self._proc
        assert proc is not None and proc.stdin is not None
        timeout = self.config.default_timeout_s if timeout_s is None else timeout_s
        payload = request.encode().encode("utf-8", "surrogateescape")
        try:
            proc.stdin.write(payload)
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self.stop()
            raise WorkerDied(f"worker pipe closed while sending: {exc}") from exc

        deadline = time.monotonic() + timeout
        lines: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.kill_now()
                raise WorkerTimeout(f"worker did not respond within {timeout:g}s")
            try:
                line = self._read_line(remaining)
            except WorkerTimeout:
                self.kill_now()
                raise WorkerTimeout(
                    f"worker did not respond within {timeout:g}s"
                ) from None
            if line is None:
                detail = "\n".join(self._stderr[-20:])
                self.stop()
                raise WorkerDied(f"worker exited mid-request. stderr:\n{detail}")
            if line == "END":
                return Response.parse(lines)
            lines.append(line)

    def kill_now(self) -> None:
        """Hard-kill. YottaDB rolls back an open TP frame on process death."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        # Close the pipes explicitly. The benchmark kills and respawns workers
        # thousands of times; leaking a descriptor per cycle exhausts the fd
        # table long before a run finishes.
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    # ------------------------------------------------------------------- io

    def _read_line(self, timeout_s: float) -> str | None:
        """Read one LF-terminated line.

        None means EOF (the process is gone). Raises WorkerTimeout if the
        deadline expires while the process is still alive -- the caller must
        not confuse the two, see WorkerTimeout.
        """
        proc = self._proc
        if proc is None or proc.stdout is None:
            return None
        fd = proc.stdout.fileno()
        deadline = time.monotonic() + timeout_s
        while True:
            nl = self._buf.find(b"\n")
            if nl >= 0:
                line, self._buf = self._buf[:nl], self._buf[nl + 1:]
                return line.decode("utf-8", "surrogateescape").rstrip("\r")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WorkerTimeout(f"no response within {timeout_s:g}s")
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                raise WorkerTimeout(f"no response within {timeout_s:g}s")
            chunk = os.read(fd, 65536)
            if not chunk:
                return None
            self._buf += chunk


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise WorkerError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}"
        )
    return proc


def copy_into_container(
    local: Path, remote: str, config: CoreConfig | None = None
) -> None:
    """docker cp a file in and hand it to the instance user."""
    cfg = config or DEFAULT_CONFIG
    _run([cfg.docker, "cp", str(local), f"{cfg.container}:{remote}"])
    _run([cfg.docker, "exec", "-u", "root", cfg.container,
          "chown", f"{cfg.instance}:{cfg.instance}", remote])
