"""Rosetta core contract. FROZEN.

Do not modify the dataclasses or signatures in this file. Every workstream builds
against it. If a change looks necessary, stop and report rather than editing.

ISOLATION: `clean_state()` is the primary mechanism and wraps the body in a YottaDB
TP frame (TSTART/TROLLBACK). `snapshot()`/`restore()` are the .dat-copy REPAIR path,
used only when a TP frame is voided or exceeds buffer space -- they cost ~20s against
a 3.4GB region and must not appear in the per-case hot loop. See docs/PROJECT.md #6.
"""

from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Literal


@dataclass(frozen=True)
class ExecSpec:
    """One execution of one routine under YottaDB."""
    routine: str
    entry: str | None = None                                    # None = top of routine
    args: list[str] = field(default_factory=list)
    locals_in: dict[str, str] = field(default_factory=dict)
    globals_in: dict[str, str] = field(default_factory=dict)    # {"^X(1)": "abc"}
    timeout_s: float = 10.0


@dataclass(frozen=True)
class ExecResult:
    """Observable result. globals_out is half the verification signal --
    a routine can return a correct value and still corrupt the database.
    error holds the MUMPS code (M6, M7...); an error IS a divergence.

    stdout is captured to a global inside the TP frame, never to a device:
    YottaDB may silently restart a transaction, and device writes are not
    rolled back, so device-captured output duplicates across restarts.

    restarts exposes $TRESTART at end of body. Any value > 0 means the body
    ran more than once; the caller must discard and re-run the case.

    void means frame integrity was lost mid-execution -- the code under test
    collapsed $TLEVEL via an unbalanced TCOMMIT or a bare TROLLBACK. A void
    result carries NO information and must never be scored. Recover with
    restore() and respawn the worker.
    """
    stdout: str
    error: str | None
    globals_out: dict[str, str]
    duration_ms: int
    restarts: int = 0
    void: bool = False


DivergenceKind = Literal["output", "global", "error", "timeout"]


@dataclass(frozen=True)
class Divergence:
    """Must name what moved. ref is "^DPT(3,0)" for kind="global",
    or "stdout" for kind="output". The repair loop needs specificity."""
    kind: DivergenceKind
    ref: str
    expected: str
    actual: str
    case_index: int = 0


@dataclass(frozen=True)
class VerifyReport:
    equivalent: bool
    divergences: list[Divergence]
    n_cases: int
    n_diverged: int
    n_void: int = 0          # cases that produced no trustworthy result

    def summary(self) -> str: ...


# --- API ---

@contextmanager
def clean_state() -> Iterator[None]:
    """PRIMARY isolation. Open a TP frame on entry, TROLLBACK on exit.

    Rollback is 22us-1.8ms and near-constant in transaction size. Use around
    EVERY execution; an unrestored run silently poisons every subsequent test.

    Preconditions the implementation must enforce:
      - hold ZERO M locks when opening the frame (TPLOCK is a hard error)
      - assert $TLEVEL on exit; if it dropped below entry level the case is void
      - name every capture local in TSTART (...) or reconstruct capture after
        the body, since a restart does not restore unlisted locals
    Raises on TRANS2BIG (~8MB / ~2000 dirty blocks); the caller falls back to
    snapshot()/restore() for that case and marks the task heavyweight.
    """


def snapshot() -> str:
    """FALLBACK repair path. Copy the YottaDB .dat region files.

    ~20s against a 3.4GB region. This is NOT the per-case mechanism -- it exists
    to recover after a voided frame or a TRANS2BIG. Returns a snapshot id.
    """


def restore(snap_id: str) -> None:
    """Restore regions captured by snapshot(). Same ~20s cost."""


def load_routine(name: str, source: str) -> None:
    """Write source into the environment and compile. Raise with M code on failure.

    MUST reject source containing command-position TSTART, TCOMMIT or TROLLBACK.
    Real VistA never uses TP (zero occurrences across 39,612 routines), but a
    mutated or model-generated candidate that emits TCOMMIT would silently commit
    the verifier's own frame to the live database with no error raised.
    """


def execute(spec: ExecSpec) -> ExecResult:
    """Run one spec in a long-lived M worker process.

    Do NOT fork per case: `docker exec` + `mumps -run` costs ~448ms, which
    dominates every realistic cycle. One supervised worker over a pipe.
    Treat worker death (HALT in the body) as error="HALT" and respawn.
    """


def verify_equivalence(
    routine: str,
    baseline_src: str,
    candidate_src: str,
    cases: list[ExecSpec],
) -> VerifyReport:
    """Decide behavioral equivalence of two versions of one routine.

    Per case, inside clean_state(): load baseline, execute, capture, roll back;
    load candidate, execute, capture, roll back. Diff stdout, error code, and
    globals_out. Collect EVERY divergence -- do not early-return, the repair
    loop wants the full picture. Void cases are counted, never scored.

    This function is the entire project. Write it boringly and test it
    against a real routine with a real mutation.
    """
