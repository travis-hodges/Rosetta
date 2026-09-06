"""The verifier. Implements ``rosetta/core/interface.py`` against real YottaDB.

Nothing here mocks the database. `clean_state()` opens a real TP frame in a
real `mumps` process; `verify_equivalence()` really runs both versions of a
routine and really diffs the globals they touched.

Read `interface.py` first -- it is the contract and it is frozen.

Deviations from a naive reading of the contract, all deliberate:

* ``ExecResult.stdout`` is device output plus, for an extrinsic entry, the
  returned value. For a pure extrinsic (no WRITE) stdout is exactly the return
  value, which is what makes ``$$VALIDUEI^PRCHUEI`` scoreable at all.
* Device output is captured by pointing ``USE`` at a truncate-on-open scratch
  file inside the container, then slurped after the rollback. YottaDB has no
  "write to a global" device. The hazard the contract warns about -- device
  output duplicating across a silent restart -- is handled by truncating at
  every OPEN and by discarding any case with ``restarts > 0``.
* ``globals_out`` mixes two tiers. A scoped ``$QUERY`` walk reports final state;
  the ``$ZTRIGGER`` tier reports final values at touched refs, with a KILLed ref
  reported as the sentinel ``rosetta.core.config.KILLED``. Both versions of a
  routine go through the identical tier, so a diff stays meaningful.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterator

from .analysis import (
    CapturePlan,
    CaptureTier,
    RoutineFacts,
    RoutineRejected,
    parse,
    plan_capture,
    reject_if_unsafe,
)
from .config import DEFAULT_CONFIG, KILLED, RESERVED_GLOBALS, CoreConfig
from .interface import Divergence, ExecResult, ExecSpec, VerifyReport
from .protocol import Request
from .worker import WorkerTimeout, MWorker, WorkerDied, WorkerError

log = logging.getLogger("rosetta.core")

__all__ = [
    "Runtime",
    "TransactionTooBig",
    "VoidExecution",
    "clean_state",
    "execute",
    "get_runtime",
    "load_routine",
    "restore",
    "snapshot",
    "verify_equivalence",
]


class TransactionTooBig(RuntimeError):
    """TRANS2BIG: the body dirtied more than the region's buffers can hold.

    Raised on the first pass, not after a restart storm. The caller falls back
    to snapshot()/restore() for that case and marks the task heavyweight.
    """


class VoidExecution(RuntimeError):
    """Frame integrity was lost. The result carries no information."""


def _summary(self: VerifyReport) -> str:
    if self.equivalent:
        return f"equivalent over {self.n_cases} case(s)"
    parts = [
        ("INCONCLUSIVE" if self.n_void else "NOT equivalent")
        + f": {self.n_diverged}/{self.n_cases} case(s) diverged",
    ]
    if self.n_void:
        parts.append(f"{self.n_void} void")
    for d in self.divergences[:10]:
        parts.append(
            f"  case {d.case_index} {d.kind} {d.ref}: "
            f"expected {d.expected!r} actual {d.actual!r}"
        )
    if len(self.divergences) > 10:
        parts.append(f"  ... and {len(self.divergences) - 10} more")
    return "\n".join(parts)


# interface.py declares summary() with no body; bind the real one here rather
# than editing the frozen file.
VerifyReport.summary = _summary  # type: ignore[method-assign]


@dataclass
class LoadedRoutine:
    name: str
    source: str
    facts: RoutineFacts
    plan: CapturePlan


class Runtime:
    """Owns the worker, the loaded routines and the capture plan."""

    def __init__(self, config: CoreConfig | None = None) -> None:
        # A fresh session per Runtime, derived from DEFAULT_CONFIG so any
        # customisation of it is preserved. Sharing the module singleton meant
        # every Runtime shared one private directory, which put concurrent
        # verifiers back on the same candidate source -- and a verifier that
        # reads somebody else's candidate returns a confident wrong verdict.
        self.config = config or replace(
            DEFAULT_CONFIG, session=f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
        self.worker = MWorker(self.config)
        self._loaded: dict[str, LoadedRoutine] = {}
        self._installed_trigger_roots: tuple[str, ...] = ()
        self._trigger_prefix = "Ros" + uuid.uuid4().hex[:16]
        self._node_counts: dict[str, int] = {}
        #: Roots whose write capture is known to be incomplete. Not part of the
        #: frozen contract, but the caller must be able to see it.
        self.incomplete_capture: list[str] = []

    # ---------------------------------------------------------------- basics

    def ping(self) -> bool:
        return self.worker.send(Request().add("CMD", "PING")).ok

    def close(self) -> None:
        try:
            self.clear_triggers()
        except (WorkerError, OSError):
            pass
        self.worker.stop()
        self._remove_sandbox()

    def _remove_sandbox(self) -> None:
        """Delete this Runtime's private routine directory.

        Each Runtime creates one, and a crashed run leaves it behind along with
        its relink control files. A real session accumulated 334 of them before
        anyone noticed, so cleanup is part of close() rather than a chore.
        Best effort: failing to tidy up must never mask the real result.
        """
        cfg = self.config
        path = cfg.private_dir
        # Refuse to rm -rf anything that is not clearly one of our sandboxes.
        if "/rt/" not in path or not cfg.session or path.count("/") < 4:
            log.warning("refusing to remove implausible sandbox path %r", path)
            return
        try:
            subprocess.run(
                [cfg.docker, "exec", cfg.container, "rm", "-rf", path],
                capture_output=True, timeout=20, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("sandbox cleanup skipped for %s: %s", path, exc)

    def __enter__(self) -> "Runtime":
        self.worker.ensure_started()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------ isolation

    @contextmanager
    def clean_state(self) -> Iterator[None]:
        """Primary isolation.

        The TP frame itself is opened and rolled back inside the worker, around
        the body of a single ``execute()`` -- a frame cannot straddle the pipe,
        because a Python-side exception between TSTART and TROLLBACK would leave
        a transaction open in a process nobody is talking to.

        What this context manager therefore guarantees is the same thing the
        contract asks for: the database looks the same on the way out as on the
        way in. On entry it asserts ``$TLEVEL == 0`` and clears the verifier's
        scratch globals; on exit it re-asserts, rolls back anything a case left
        open, and clears scratch again. Every ``execute()`` inside is
        individually transaction-wrapped and individually rolled back.
        """
        self._reset("entering clean_state")
        try:
            yield
        finally:
            self._reset("leaving clean_state")

    def _reset(self, why: str) -> None:
        try:
            resp = self.worker.send(Request().add("CMD", "RESET"))
        except WorkerDied:
            log.warning("worker died %s; respawning", why)
            self.worker.ensure_started()
            resp = self.worker.send(Request().add("CMD", "RESET"))
        tlevel = resp.get_int("TLEVEL", -1)
        if tlevel != 0:
            raise VoidExecution(
                f"$TLEVEL is {tlevel} {why}; the environment is not isolated"
            )

    # -------------------------------------------------------- snapshot/restore

    def _database_maintenance(self, command: str) -> str:
        """Run maintenance exclusively; never replace an attached database."""
        cfg = self.config
        script = (
            "set -e; "
            'if pgrep -u "$(id -u)" -x mumps >/dev/null; then '
            'echo "database maintenance requires all M workers to stop" >&2; exit 1; fi; '
            f"source {shlex.quote(cfg.env_file)}; "
            '"$gtm_dist/mupip" rundown -region "*"; ' + command
        )
        return self._sh(
            f"flock --exclusive --nonblock {shlex.quote(cfg.scratch_dir + '/database.lock')} "
            f"bash -c {shlex.quote(script)}"
        )

    def snapshot(self) -> str:
        """Create a ready-to-run MUPIP backup with independent journal state."""
        snap_id = f"snap-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
        dest = f"{self.config.snapshot_dir}/{snap_id}"
        regions = self.regions()
        if not regions:
            raise WorkerError("no regions discovered; run scripts/bootstrap.sh")
        self.clear_triggers()
        self.worker.stop()
        started = time.monotonic()
        try:
            self._database_maintenance(
                f"mkdir -p {shlex.quote(dest)}; "
                '"$gtm_dist/mupip" backup -database -noonline -bkupdbjnl=disable '
                f"-nonewjnlfiles '*' {shlex.quote(dest + '/')}; "
                f"touch {shlex.quote(dest + '/.complete')}"
            )
        finally:
            self.worker.ensure_started()
        log.info("snapshot %s captured %d regions in %.1fs",
                 snap_id, len(regions), time.monotonic() - started)
        return snap_id

    def online_snapshot(self) -> str:
        """Capture a consistent rollback point while editor workers stay live.

        YottaDB's DATABASE ONLINE backup is a point-in-time backup and does
        not require standalone access. Verification's ``snapshot()`` remains
        the quiesced repair path; persistent editor changes use this online
        variant so an open TUI cannot deadlock its own change workflow.
        """
        snap_id = f"snap-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
        dest = f"{self.config.snapshot_dir}/{snap_id}"
        if not self.regions():
            raise WorkerError("no regions discovered; run scripts/bootstrap.sh")
        command = (
            f"mkdir -p {shlex.quote(dest)}; "
            f"source {shlex.quote(self.config.env_file)}; "
            '"$gtm_dist/mupip" backup -database -online -bkupdbjnl=disable '
            f"-nonewjnlfiles '*' {shlex.quote(dest + '/')}; "
            f"touch {shlex.quote(dest + '/.complete')}"
        )
        started = time.monotonic()
        self._sh(
            f"flock --shared {shlex.quote(self.config.scratch_dir + '/database.lock')} "
            f"bash -c {shlex.quote('set -e; ' + command)}"
        )
        log.info("online snapshot %s captured in %.1fs", snap_id, time.monotonic() - started)
        return snap_id

    def restore(self, snap_id: str) -> None:
        """Restore a completed snapshot with exclusive database access."""
        if not re.fullmatch(r"snap-[0-9]{8}T[0-9]{6}-[a-f0-9]{8}", snap_id):
            raise ValueError("invalid snapshot identifier")
        src = f"{self.config.snapshot_dir}/{snap_id}"
        regions = self.regions()
        if not regions:
            raise WorkerError("no regions discovered")
        # Validate the complete snapshot before stopping or changing anything.
        self._sh(" && ".join(
            [f"test -f {shlex.quote(src + '/.complete')}"]
            + [f"test -s {shlex.quote(src + '/' + os.path.basename(path))}"
               for _, path in regions]
        ))
        self.worker.stop()
        # A failed restore must never leave executable cached routine plans.
        self._loaded.clear()
        self._node_counts.clear()
        self._installed_trigger_roots = ()
        started = time.monotonic()
        commands = []
        staged = []
        for _, path in regions:
            temporary = path + ".restore-" + uuid.uuid4().hex[:8]
            staged.append((temporary, path))
            commands.append(
                f"cp --reflink=auto -p {shlex.quote(src + '/' + os.path.basename(path))} "
                f"{shlex.quote(temporary)}"
            )
        commands.extend(f"mv -f {shlex.quote(temporary)} {shlex.quote(path)}"
                        for temporary, path in staged)
        self._database_maintenance("; ".join(commands))
        self.worker.ensure_started()
        self._reset("after snapshot restore")
        log.info("restored %s in %.1fs", snap_id, time.monotonic() - started)

    def regions(self) -> list[tuple[str, str]]:
        """(region, .dat path) pairs from the bootstrap manifest."""
        proc = subprocess.run(
            [self.config.docker, "exec", "-u", self.config.instance,
             self.config.container, "cat", self.config.region_manifest],
            capture_output=True, text=True,
        )
        out: list[tuple[str, str]] = []
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if "\t" in line:
                    region, path = line.split("\t", 1)
                    if path.strip().endswith(".dat"):
                        out.append((region.strip(), path.strip()))
        if not out:
            listing = subprocess.run(
                [self.config.docker, "exec", "-u", self.config.instance,
                 self.config.container, "bash", "-c",
                 f"ls {self.config.basedir}/g/*.dat"],
                capture_output=True, text=True,
            )
            for path in listing.stdout.split():
                out.append((Path(path).stem.upper(), path))
        return out

    def database_status(self) -> dict[str, object]:
        """Operational facts an editor should show before a persistent apply."""
        regions = self.regions()
        proc = subprocess.run(
            [self.config.docker, "exec", "-u", self.config.instance,
             self.config.container, "bash", "-c",
             'pgrep -u "$(id -u)" -x mumps | wc -l'],
            capture_output=True, text=True, timeout=20,
        )
        try:
            attached = int(proc.stdout.strip()) if proc.returncode == 0 else -1
        except ValueError:
            attached = -1
        return {
            "container": self.config.container,
            "instance": self.config.instance,
            "regions": len(regions),
            "attached_m_processes": attached,
            "snapshot_mode": "MUPIP DATABASE ONLINE",
            "persistent_apply": "FileMan DBS API or atomic exact-node transaction",
        }

    # --------------------------------------------------------- database edit

    def read_globals(self, refs: list[str] | tuple[str, ...]) -> dict[str, tuple[bool, str]]:
        """Read exact global nodes outside the verifier transaction.

        Reference grammar and authorization live in :mod:`rosetta.database`.
        The worker receives only validated literal references; this method is
        intentionally narrow so editor code cannot smuggle arbitrary M into an
        XECUTE boundary.
        """
        if not refs:
            return {}
        self.worker.ensure_started()
        req = Request().add("CMD", "GETG")
        for ref in refs:
            req.add("REF", ref)
        resp = self.worker.send(req, timeout_s=30.0)
        if not resp.ok:
            raise WorkerError(resp.get("ERROR") or "database read failed")
        names = resp.get_all("GR")
        present = resp.get_all("GP")
        values = resp.get_all("GV")
        if not (len(names) == len(present) == len(values) == len(refs)):
            raise WorkerError("malformed database read response")
        return {
            name: (flag == "1", value)
            for name, flag, value in zip(names, present, values)
        }

    def apply_global_changes(
        self, changes: list[dict[str, str]] | tuple[dict[str, str], ...]
    ) -> dict[str, tuple[bool, str]]:
        """Atomically commit an explicitly authorized set of exact-node edits.

        This is the persistent counterpart to ``execute()``. It never loads or
        runs customer source. The M worker first checks every pinned before
        value, then commits all SET/KILL operations in one transaction. A
        caller must create a full snapshot before reaching this method.
        """
        if not changes:
            raise ValueError("at least one database change is required")
        self.worker.ensure_started()
        req = Request().add("CMD", "APPLYG")
        for change in changes:
            req.add("OP", change["op"].upper())
            req.add("REF", change["ref"])
            req.add("VALUE", change.get("value", ""))
            req.add("BEFOREP", change["before_present"])
            req.add("BEFOREV", change.get("before_value", ""))
        resp = self.worker.send(req, timeout_s=30.0)
        if resp.status == "CONFLICT":
            raise WorkerError(
                "database precondition failed for "
                + ", ".join(resp.get_all("CONFLICT"))
            )
        if not resp.ok:
            raise WorkerError(resp.get("ERROR") or "database apply failed")
        return self.read_globals(tuple(change["ref"] for change in changes))

    def apply_fileman_record(
        self, file_number: str, iens: str, fields: dict[str, str]
    ) -> dict[str, object]:
        """Persist one record through FileMan's supported DBS filer APIs."""
        if not fields:
            raise ValueError("at least one FileMan field is required")
        self.worker.ensure_started()
        req = (Request().add("CMD", "FILEMAN")
               .add("FILE", file_number).add("IENS", iens))
        for field, value in fields.items():
            req.add("FIELD", field).add("VALUE", value)
        resp = self.worker.send(req, timeout_s=60.0)
        if not resp.ok:
            raise WorkerError(resp.get("ERROR") or "FileMan rejected the change")
        names, values = resp.get_all("FIELD"), resp.get_all("VALUE")
        if len(names) != len(values):
            raise WorkerError("malformed FileMan response")
        return {
            "file": file_number,
            "ien": resp.get("IEN"),
            "fields": dict(zip(names, values)),
            "persistent": True,
            "api": "UPDATE^DIE" if iens.startswith("+") else "FILE^DIE",
        }

    def _sh(self, command: str) -> str:
        proc = subprocess.run(
            [self.config.docker, "exec", "-u", self.config.instance,
             self.config.container, "bash", "-c", command],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise WorkerError(f"in-container command failed: {command}\n{proc.stderr}")
        return proc.stdout

    # -------------------------------------------------------------- routines

    def _capture_facts(self, facts: RoutineFacts) -> RoutineFacts:
        """Include statically reachable callees in the observable write set.

        A missing or dynamically addressed callee cannot support a trustworthy
        verdict. Refuse it rather than silently inspecting only the caller.
        """
        from .source_io import container_files

        seen = {facts.name}
        pending = list(facts.facts.get("calls", []))
        writes = set(facts.globals_written)
        emits_output = facts.writes_device
        capture_depth = facts.max_global_depth
        has_merge = facts.facts.get("commands", {}).get("MERGE", 0)
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            if len(seen) >= 64:
                raise RoutineRejected("call graph exceeds the 64-routine capture limit")
            seen.add(name)
            if name in self._loaded:
                child = self._loaded[name].facts
            else:
                filename = ("_" + name[1:] if name.startswith("%") else name) + ".m"
                try:
                    source = container_files(
                        self.config.container,
                        ["cat", f"{self.config.routine_dir}/{filename}"],
                    )
                except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                    raise RoutineRejected(f"cannot inspect callee {name}: {exc}") from exc
                child = parse(name, source)
            reject_if_unsafe(name, child.source)
            if not child.write_set_is_bounded:
                raise RoutineRejected(f"callee {name} has an unbounded write set")
            writes.update(child.globals_written)
            capture_depth = max(capture_depth, child.max_global_depth)
            has_merge = has_merge or child.facts.get("commands", {}).get("MERGE", 0)
            emits_output = emits_output or child.writes_device
            pending.extend(child.facts.get("calls", []))
        merged = dict(facts.facts)
        merged["globals_written"] = sorted(writes)
        merged["commands"] = dict(merged.get("commands", {}))
        merged["capture_global_depth"] = capture_depth
        merged["capture_has_merge"] = bool(has_merge)
        if emits_output:
            merged["commands"]["WRITE"] = max(1, merged["commands"].get("WRITE", 0))
        return replace(facts, facts=merged)

    def load_routine(self, name: str, source: str) -> None:
        """Write source into the environment and compile it.

        Rejects command-position TP commands before anything touches the
        container. Raises with the M error code on a compile failure.
        """
        if not name or not name.replace("%", "").isalnum():
            raise ValueError(f"implausible routine name: {name!r}")
        reject_if_unsafe(name, source)

        # The private sandbox is created when the worker starts, and
        # load_routine is reachable before the first execute().
        self.worker.ensure_started()

        facts = self._capture_facts(parse(name, source))
        plan = plan_capture(
            facts,
            node_counts=self._measure_roots(facts),
            query_tier_node_cap=self.config.query_tier_node_cap,
        )
        if plan.trigger_roots and (
            facts.max_global_depth > self.config.trigger_depth
            or facts.facts.get("capture_has_merge")
        ):
            raise RoutineRejected(
                f"{name}: trigger capture cannot certify this write set "
                f"(depth limit {self.config.trigger_depth}); "
                "MERGE or deeper references require a query capture plan"
            )
        if not plan.complete:
            msg = f"{name}: {plan.reason}"
            if msg not in self.incomplete_capture:
                self.incomplete_capture.append(msg)
            raise RoutineRejected(f"cannot verify incomplete globals_out capture -- {msg}")
        self._loaded.pop(name, None)
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / f"{name}.m"
            local.write_text(source, encoding="utf-8")
            # Private, not {routine_dir}: a shared source directory means two
            # verifiers working on the same routine name overwrite each other,
            # which yields a confident verdict for somebody else's candidate.
            self._copy_in(local, f"{self.config.private_src}/{name}.m")

        resp = self.worker.send(Request().add("CMD", "LINK").add("ROUTINE", name))
        if not resp.ok:
            raise RuntimeError(
                f"compile/link failed for {name}: {resp.get('ERROR') or resp.get('ZSTATUS')}"
            )

        self._loaded[name] = LoadedRoutine(name, source, facts, plan)
        self._sync_triggers(plan)

    def _copy_in(self, local: Path, remote: str) -> None:
        cfg = self.config
        for cmd in (
            [cfg.docker, "cp", str(local), f"{cfg.container}:{remote}"],
            [cfg.docker, "exec", "-u", "root", cfg.container,
             "chown", f"{cfg.instance}:{cfg.instance}", remote],
        ):
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise WorkerError(f"{' '.join(cmd)} failed: {proc.stderr.strip()}")

    def _measure_roots(self, facts: RoutineFacts) -> dict[str, int]:
        roots = [r for r in facts.globals_written if r not in RESERVED_GLOBALS]
        todo = [r for r in roots if r not in self._node_counts]
        if todo:
            req = Request().add("CMD", "COUNT").add("MAX", self.config.query_tier_node_cap + 1)
            for r in todo:
                req.add("ROOT", r)
            resp = self.worker.send(req, timeout_s=30.0)
            for entry in resp.get_all("N"):
                ref, _, count = entry.rpartition("=")
                if ref:
                    self._node_counts[ref] = int(count)
            for r in todo:
                self._node_counts.setdefault(r, self.config.query_tier_node_cap + 1)
        return {r: self._node_counts[r] for r in roots if r in self._node_counts}

    # -------------------------------------------------------------- triggers

    def _sync_triggers(self, plan: CapturePlan) -> None:
        wanted = tuple(sorted(plan.trigger_roots))
        if wanted == self._installed_trigger_roots:
            return
        self.clear_triggers()
        if not wanted:
            return
        # $ZTRIGGER inside TP silently no-ops; install before any TSTART.
        req = (Request().add("CMD", "TRIG").add("MODE", "INSTALL")
               .add("DEPTH", self.config.trigger_depth)
               .add("PREFIX", self._trigger_prefix))
        for root in wanted:
            req.add("ROOT", root)
        resp = self.worker.send(req, timeout_s=60.0)
        if not resp.ok:
            raise WorkerError(
                f"trigger install failed for {wanted}: "
                f"{resp.get_int('FAILED')} of "
                f"{resp.get_int('FAILED') + resp.get_int('INSTALLED')} definitions"
            )
        self._installed_trigger_roots = wanted
        log.info("installed capture triggers on %s (depth %d)",
                 ", ".join(wanted), self.config.trigger_depth)

    def clear_triggers(self) -> None:
        response = self.worker.send(
            Request().add("CMD", "TRIG").add("MODE", "CLEAR")
            .add("PREFIX", self._trigger_prefix), timeout_s=60.0,
        )
        if not response.ok:
            raise WorkerError("failed to remove this runtime's capture triggers")
        self._installed_trigger_roots = ()

    # ------------------------------------------------------------- execution

    def execute(self, spec: ExecSpec) -> ExecResult:
        """Run one spec in the long-lived worker."""
        loaded = self._loaded.get(spec.routine)
        if loaded is None:
            raise KeyError(
                f"{spec.routine} has not been loaded; call load_routine() first"
            )
        self._sync_triggers(loaded.plan)
        entry = spec.entry
        extrinsic = loaded.facts.is_extrinsic(entry)
        if entry and entry.startswith("$$"):
            entry = entry[2:]

        req = (
            Request()
            .add("CMD", "EXEC")
            .add("ROUTINE", spec.routine)
            .add("ENTRY", entry or "")
            .add("EXTRINSIC", 1 if extrinsic else 0)
            .add("CAPOUT", 1 if loaded.facts.writes_device else 0)
            .add("MAXNODES", self.config.max_nodes_per_root)
        )
        for arg in spec.args:
            req.add("ARG", arg)
        for name, value in spec.locals_in.items():
            req.add("LOCALN", name).add("LOCALV", value)
        for ref, value in spec.globals_in.items():
            req.add("GLOBALR", ref).add("GLOBALV", value)
        for root in loaded.plan.query_roots:
            req.add("WATCH", root)
        # Anything the caller seeded is by definition a scoped subtree we can
        # afford to walk, even if the root as a whole is huge.
        for ref in spec.globals_in:
            req.add("WATCH", ref)

        started = time.monotonic()
        try:
            resp = self.worker.send(req, timeout_s=max(spec.timeout_s, 1.0) + 5.0)
        except (WorkerDied, WorkerTimeout) as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            log.warning("worker died running %s: %s", spec.routine, exc)
            self.worker.ensure_started()
            self._installed_trigger_roots = ()
            self._sync_triggers(loaded.plan)
            kind = "TIMEOUT" if isinstance(exc, WorkerTimeout) else "HALT"
            return ExecResult(
                stdout="", error=kind, globals_out={},
                duration_ms=elapsed, restarts=0, void=True,
            )

        status = resp.status
        if status == "VOID":
            return ExecResult(
                stdout="", error=resp.get("REASON") or "VOID",
                globals_out={}, duration_ms=int(resp.get_int("DURUS") / 1000),
                restarts=resp.get_int("RESTARTS"), void=True,
            )
        if status == "ERR":
            # An error the worker itself hit outside the body -- protocol or
            # environment, never the routine's own error. Fail loudly.
            raise WorkerError(
                f"worker rejected EXEC for {spec.routine}: "
                f"{resp.get('ERROR') or resp.get('ZSTATUS')}"
            )

        error = resp.get("ERROR") or None
        if error and "TRANS2BIG" in error:
            raise TransactionTooBig(
                f"{spec.routine}: {error}. Fall back to snapshot()/restore() "
                f"for this case and mark the task heavyweight."
            )
        if resp.get_int("TRUNCATED"):
            log.warning(
                "%s: incomplete globals_out capture (%s)",
                spec.routine, resp.get("REASON") or f"watched root exceeds {self.config.max_nodes_per_root} nodes",
            )

        refs = resp.get_all("GR")
        vals = resp.get_all("GV")
        if len(refs) != len(vals):
            raise WorkerError(
                f"malformed globals_out for {spec.routine}: "
                f"{len(refs)} refs but {len(vals)} values"
            )
        globals_out = {
            ref: val for ref, val in zip(refs, vals)
            if not any(ref == g or ref.startswith(g + "(") for g in RESERVED_GLOBALS)
        }

        return ExecResult(
            stdout=resp.get("STDOUT"),
            error=error,
            globals_out=globals_out,
            duration_ms=max(0, int(resp.get_int("DURUS") / 1000)),
            restarts=resp.get_int("RESTARTS"),
            void=bool(resp.get_int("TRUNCATED")),
        )

    def execute_stable(self, spec: ExecSpec, attempts: int = 3) -> ExecResult:
        """execute() plus the restart discard the contract requires.

        A result with ``restarts > 0`` means the body ran more than once, so
        locals not named in TSTART were not restored and any device output was
        produced twice. Such a result is discarded and the case re-run.
        """
        result = self.execute(spec)
        for attempt in range(1, attempts):
            if result.restarts == 0:
                return result
            log.warning(
                "%s: $TRESTART=%d, discarding and re-running (attempt %d/%d)",
                spec.routine, result.restarts, attempt + 1, attempts,
            )
            result = self.execute(spec)
        if result.restarts:
            log.error(
                "%s: still restarting after %d attempts; the container is not "
                "quiesced. Run scripts/bootstrap.sh --measure.",
                spec.routine, attempts,
            )
            return replace(result, void=True)
        return result

    # ------------------------------------------------------------- equivalence

    def verify_equivalence(
        self,
        routine: str,
        baseline_src: str,
        candidate_src: str,
        cases: list[ExecSpec],
    ) -> VerifyReport:
        """Decide behavioral equivalence of two versions of one routine."""
        if not cases:
            raise ValueError("verify_equivalence needs at least one case")

        divergences: list[Divergence] = []
        n_void = 0
        diverged_cases = 0

        try:
            with self.clean_state():
                for index, raw_spec in enumerate(cases):
                    spec = replace(raw_spec, routine=routine)

                    self.load_routine(routine, baseline_src)
                    base = self.execute_stable(spec)

                    self.load_routine(routine, candidate_src)
                    cand = self.execute_stable(spec)

                    if base.void or cand.void or base.restarts or cand.restarts:
                        n_void += 1
                        log.warning(
                            "case %d is void (baseline=%s candidate=%s); not scored",
                            index, base.error, cand.error,
                        )
                        continue

                    found = diff_results(base, cand, index)
                    if found:
                        diverged_cases += 1
                        divergences.extend(found)
        finally:
            # Source restoration also runs after compilation or execution errors.
            self.load_routine(routine, baseline_src)

        return VerifyReport(
            equivalent=not divergences and n_void == 0,
            divergences=divergences,
            n_cases=len(cases),
            n_diverged=diverged_cases,
            n_void=n_void,
        )


def diff_results(base: ExecResult, cand: ExecResult, case_index: int = 0) -> list[Divergence]:
    """Every difference between two results. Never early-returns."""
    out: list[Divergence] = []

    if base.stdout != cand.stdout:
        out.append(Divergence(
            kind="output", ref="stdout",
            expected=base.stdout, actual=cand.stdout, case_index=case_index,
        ))

    if (base.error or "") != (cand.error or ""):
        kind = "timeout" if "TIMEOUT" in {base.error, cand.error} else "error"
        out.append(Divergence(
            kind=kind, ref="error",
            expected=base.error or "", actual=cand.error or "",
            case_index=case_index,
        ))

    for ref in sorted(set(base.globals_out) | set(cand.globals_out)):
        want = base.globals_out.get(ref, _ABSENT)
        got = cand.globals_out.get(ref, _ABSENT)
        if want != got:
            out.append(Divergence(
                kind="global", ref=ref,
                expected=_render(want), actual=_render(got),
                case_index=case_index,
            ))
    return out


_ABSENT = object()


def _render(value: object) -> str:
    if value is _ABSENT:
        return "<absent>"
    if value == KILLED:
        return "<killed>"
    return str(value)


# --------------------------------------------------------------- module API
# interface.py declares these as free functions. A process-wide Runtime backs
# them so callers who do not want to manage a worker do not have to.

_RUNTIME: Runtime | None = None


def get_runtime(config: CoreConfig | None = None) -> Runtime:
    """The process-wide Runtime, created on first use."""
    global _RUNTIME
    if _RUNTIME is None or (config is not None and config != _RUNTIME.config):
        if _RUNTIME is not None:
            _RUNTIME.close()
        _RUNTIME = Runtime(config)
        _RUNTIME.worker.ensure_started()
    return _RUNTIME


def shutdown() -> None:
    global _RUNTIME
    if _RUNTIME is not None:
        _RUNTIME.close()
        _RUNTIME = None


@contextmanager
def clean_state() -> Iterator[None]:
    with get_runtime().clean_state():
        yield


def snapshot() -> str:
    return get_runtime().snapshot()


def restore(snap_id: str) -> None:
    get_runtime().restore(snap_id)


def load_routine(name: str, source: str) -> None:
    get_runtime().load_routine(name, source)


def execute(spec: ExecSpec) -> ExecResult:
    return get_runtime().execute_stable(spec)


def verify_equivalence(
    routine: str,
    baseline_src: str,
    candidate_src: str,
    cases: list[ExecSpec],
) -> VerifyReport:
    return get_runtime().verify_equivalence(routine, baseline_src, candidate_src, cases)
