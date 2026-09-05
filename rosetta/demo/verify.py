"""Demo-side adapter over ``rosetta.core.verify_equivalence``.

This module decides nothing. Equivalence is decided by the frozen contract's
``verify_equivalence`` running real MUMPS against the real database; everything
here is scheduling and presentation.

Why it is not just one ``verify_equivalence`` call
--------------------------------------------------
Against ``rosetta-verify`` (YottaDB r2.06), relinking a routine fails with

    %YDB-E-INVOBJFILE, Cannot ZLINK object file
    /home/vehu/r/r2.06_x86_64/AJETIU2.o due to unexpected format

``rosetta.core.load_routine`` copies the source into ``/home/vehu/r`` and
ZLINKs it, which rewrites the object file **in place** in a directory every
verifier process links from. Two consequences, both observed:

* Within one process, ``verify_equivalence`` links twice per case, and a
  multi-case task can die partway through.
* Across processes, any other Rosetta run sharing the container -- a
  ``rosetta.bench`` build, a second demo -- can pull the object out from under
  a link at any moment. This was observed directly: 27 concurrent ``ROSWRK``
  workers from a parallel bench build made every AJETIU2 link fail.

So this module runs one case per worker and retries. Even that is not enough
under heavy contention, which is why :mod:`rosetta.demo.repair_loop` goes
further and gives each case its own MCP server *process*. The real fix -- an
object directory private to each verifier process -- belongs in
``rosetta/core``.

``ROSETTA_DEMO_CASES_PER_WORKER=0`` puts all cases back in one call once core
stops relinking in place.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from rosetta.core.interface import Divergence, ExecSpec

from .tasks import DemoTask

__all__ = [
    "CaseVerdict",
    "TaskVerdict",
    "VerifierUnavailable",
    "cases_per_worker",
    "verifier_status",
    "verify_candidate",
]

log = logging.getLogger(__name__)

#: Attempts per case before giving up. See the retry comment in verify_candidate.
_RETRIES = 3
_RETRY_PAUSE_S = 1.1


class VerifierUnavailable(RuntimeError):
    """The container, docker or the M worker is not usable right now.

    Distinct from "the candidate is wrong". The demo must never render this as
    a red verdict: no verdict was reached.
    """


def cases_per_worker() -> int:
    """Cases to run per worker process. 0 means "all of them in one call"."""
    raw = os.environ.get("ROSETTA_DEMO_CASES_PER_WORKER", "1").strip()
    try:
        value = int(raw)
    except ValueError:
        log.warning("ignoring ROSETTA_DEMO_CASES_PER_WORKER=%r", raw)
        return 1
    return max(value, 0)


@dataclass(frozen=True)
class CaseVerdict:
    """What the verifier said about one input."""

    label: str
    why: str
    equivalent: bool
    divergences: list[Divergence] = field(default_factory=list)
    n_void: int = 0
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "why": self.why,
            "equivalent": self.equivalent,
            "n_void": self.n_void,
            "duration_ms": self.duration_ms,
            "divergences": [
                {
                    "kind": d.kind,
                    "ref": d.ref,
                    "expected": d.expected,
                    "actual": d.actual,
                    "case_index": d.case_index,
                }
                for d in self.divergences
            ],
        }


@dataclass(frozen=True)
class TaskVerdict:
    """Aggregate of every case the verifier replayed for one candidate."""

    task_id: str
    routine: str
    cases: list[CaseVerdict]
    duration_ms: int
    source: str = "live"  # "live" = executed just now; "canned" = replayed

    @property
    def equivalent(self) -> bool:
        return all(c.equivalent for c in self.cases) and bool(self.cases)

    @property
    def n_cases(self) -> int:
        return len(self.cases)

    @property
    def n_diverged(self) -> int:
        return sum(1 for c in self.cases if not c.equivalent)

    @property
    def n_void(self) -> int:
        return sum(c.n_void for c in self.cases)

    @property
    def divergences(self) -> list[Divergence]:
        return [d for c in self.cases for d in c.divergences]

    def headline(self) -> str:
        if not self.cases:
            return f"NO VERDICT — {self.routine}: no cases were replayed."
        if self.equivalent:
            return (
                f"EQUIVALENT — {self.routine} candidate matched the baseline on "
                f"all {self.n_cases} case(s)."
            )
        return (
            f"NOT EQUIVALENT — {self.routine} candidate diverged on "
            f"{self.n_diverged} of {self.n_cases} case(s)."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "routine": self.routine,
            "equivalent": self.equivalent,
            "n_cases": self.n_cases,
            "n_diverged": self.n_diverged,
            "n_void": self.n_void,
            "duration_ms": self.duration_ms,
            "source": self.source,
            "headline": self.headline(),
            "cases": [c.to_dict() for c in self.cases],
        }


def verifier_status() -> tuple[bool, str]:
    """(usable, reason). Cheap probe so the demo can choose a path up front."""
    try:
        from rosetta.core import get_runtime
    except Exception as exc:  # pragma: no cover - import-time environment fault
        return False, f"rosetta.core did not import: {exc}"
    try:
        runtime = get_runtime()
        runtime.worker.ensure_started()
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, "verifier reachable"


def _chunks(specs: Sequence[ExecSpec], size: int) -> list[list[ExecSpec]]:
    if size <= 0:
        return [list(specs)]
    return [list(specs[i : i + size]) for i in range(0, len(specs), size)]


def verify_candidate(
    task: DemoTask,
    candidate_src: str,
    baseline_src: str | None = None,
) -> TaskVerdict:
    """Run the real verifier over every case of ``task``.

    Raises :class:`VerifierUnavailable` when no verdict could be reached. A
    reached verdict of "not equivalent" is a normal return, not an error.
    """
    try:
        from rosetta.core import shutdown, verify_equivalence
    except Exception as exc:  # pragma: no cover
        raise VerifierUnavailable(f"rosetta.core did not import: {exc}") from exc

    baseline = baseline_src if baseline_src is not None else task.baseline_source()
    size = cases_per_worker()
    started = time.monotonic()
    verdicts: list[CaseVerdict] = []
    index = 0

    for group in _chunks(task.specs(), size):
        case_started = time.monotonic()
        report = None
        errors: list[str] = []
        # The relink fault is timing-sensitive as well as count-sensitive: the
        # .m is copied in and ZLINKed within the same second, and YottaDB
        # sometimes links the stale object instead of recompiling. A fresh
        # worker and a short pause clears it. Retry a bounded number of times
        # and then give up loudly -- never silently score a case that did not
        # actually run.
        for attempt in range(_RETRIES):
            try:
                report = verify_equivalence(
                    task.routine, baseline, candidate_src, group
                )
                break
            except Exception as exc:
                errors.append(f"attempt {attempt + 1}: {type(exc).__name__}: {exc}")
                log.warning(
                    "%s cases %d..%d: %s", task.routine, index,
                    index + len(group) - 1, errors[-1],
                )
            finally:
                if size:
                    try:
                        shutdown()
                    except Exception as exc:  # pragma: no cover - best effort
                        log.warning("worker shutdown between cases failed: %s", exc)
            time.sleep(_RETRY_PAUSE_S)

        if report is None:
            raise VerifierUnavailable(
                f"{task.routine}: verifier could not reach a verdict "
                f"(cases {index}..{index + len(group) - 1}) after {_RETRIES} "
                "attempts:\n  " + "\n  ".join(errors)
            )

        elapsed = int((time.monotonic() - case_started) * 1000)
        by_case: dict[int, list[Divergence]] = {}
        for div in report.divergences:
            by_case.setdefault(div.case_index, []).append(div)

        for offset in range(len(group)):
            case = task.cases[index + offset]
            found = by_case.get(offset, [])
            verdicts.append(
                CaseVerdict(
                    label=case.label,
                    why=case.why,
                    equivalent=not found,
                    divergences=found,
                    n_void=report.n_void if len(group) == 1 else 0,
                    duration_ms=elapsed // max(len(group), 1),
                )
            )
        index += len(group)

    return TaskVerdict(
        task_id=task.task_id,
        routine=task.routine,
        cases=verdicts,
        duration_ms=int((time.monotonic() - started) * 1000),
        source="live",
    )
