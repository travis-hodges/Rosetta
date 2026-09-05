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
  the ``$ZTRIGGER`` tier reports the last write to each ref, with a KILLed ref
  reported as the sentinel ``rosetta.core.config.KILLED``. Both versions of a
  routine go through the identical tier, so a diff stays meaningful.
"""

from __future__ import annotations

import logging
import os
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
        f"NOT equivalent: {self.n_diverged}/{self.n_cases} case(s) diverged",
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

    def snapshot(self) -> str:
        """Copy the region .dat files. ~20s against a 3.4GB region."""
        snap_id = f"snap-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
        dest = f"{self.config.snapshot_dir}/{snap_id}"
        regions = self.regions()
        if not regions:
            raise WorkerError("no regions discovered; run scripts/bootstrap.sh")
        started = time.monotonic()
        self._sh(f"mkdir -p {dest}")
        for _region, path in regions:
            self._sh(f"cp -p {path} {dest}/")
        log.info(
            "snapshot %s captured %d region(s) in %.1fs",
            snap_id, len(regions), time.monotonic() - started,
        )
        return snap_id

    def restore(self, snap_id: str) -> None:
        """Restore regions captured by snapshot(). Same ~20s cost.

        The worker is stopped first: YottaDB will not tolerate the .dat file
        under an attached process being replaced, and a restore only happens
        after a voided frame or a TRANS2BIG, when the worker is suspect anyway.
        """
        src = f"{self.config.snapshot_dir}/{snap_id}"
        probe = subprocess.run(
            [self.config.docker, "exec", self.config.container, "test", "-d", src],
            capture_output=True,
        )
        if probe.returncode != 0:
            raise WorkerError(f"no such snapshot: {snap_id}")
        self.worker.stop()
        started = time.monotonic()
        for _region, path in self.regions():
            name = os.path.basename(path)
            self._sh(f"test -f {src}/{name} && cp -p {src}/{name} {path}")
        self._loaded.clear()
        self._installed_trigger_roots = ()
        self.worker.ensure_started()
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

        facts = parse(name, source)
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

        plan = plan_capture(
            facts,
            node_counts=self._measure_roots(facts),
            query_tier_node_cap=self.config.query_tier_node_cap,
        )
        if not plan.complete:
            msg = f"{name}: {plan.reason}"
            log.warning("incomplete globals_out capture -- %s", msg)
            if msg not in self.incomplete_capture:
                self.incomplete_capture.append(msg)
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
               .add("DEPTH", self.config.trigger_depth))
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
        self.worker.send(Request().add("CMD", "TRIG").add("MODE", "CLEAR"),
                         timeout_s=60.0)
        self._installed_trigger_roots = ()

    # ------------------------------------------------------------- execution

    def execute(self, spec: ExecSpec) -> ExecResult:
        """Run one spec in the long-lived worker."""
        loaded = self._loaded.get(spec.routine)
        if loaded is None:
            raise KeyError(
                f"{spec.routine} has not been loaded; call load_routine() first"
            )
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
                "%s: a watched global root exceeded %d nodes; globals_out is truncated",
                spec.routine, self.config.max_nodes_per_root,
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
            void=False,
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

        with self.clean_state():
            for index, raw_spec in enumerate(cases):
                spec = replace(raw_spec, routine=routine)

                self.load_routine(routine, baseline_src)
                base = self.execute_stable(spec)

                self.load_routine(routine, candidate_src)
                cand = self.execute_stable(spec)

                if base.void or cand.void:
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

        # Leave the environment holding the baseline, never the candidate.
        self.load_routine(routine, baseline_src)

        return VerifyReport(
            equivalent=not divergences,
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
