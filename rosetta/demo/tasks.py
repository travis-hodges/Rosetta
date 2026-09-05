"""Demo tasks. Real VistA routines, real inputs, real divergences.

A demo task is a *change request* against one routine plus the input cases the
verifier replays. Nothing here is synthetic: the baseline is the shipped VistA
source in ``data/routines/``, the DFNs are patients that exist in the VEHU
database, and the divergence a wrong candidate produces was observed, not
written down.

The money moment lives in :data:`NOK_TASK`. ``NOK^AJETIU2`` reads
``^DPT(DFN,.21)`` -- the next-of-kin node of the PATIENT file -- and returns the
next-of-kin name, substituting ``"Not Entered"`` when the name is blank. In VEHU
765 patients have a ``.21`` node and 69 of those have the node present with an
empty name piece, so the two branches are both live on real data. That 69 is
what makes the plausible-looking rewrite wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rosetta.core.interface import ExecSpec

__all__ = ["DemoCase", "DemoTask", "NOK_TASK", "TASKS", "get_task", "repo_root"]


def repo_root() -> Path:
    """Repository root, derived from this file's location."""
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class DemoCase:
    """One input the verifier replays against both versions.

    ``why`` is narration for the audience -- it is never used to decide
    anything. The verdict comes from the verifier alone.
    """

    spec: ExecSpec
    label: str
    why: str


@dataclass(frozen=True)
class DemoTask:
    """A change request an agent is asked to carry out on one routine."""

    task_id: str
    routine: str
    entry: str
    title: str
    request: str
    cases: list[DemoCase]
    #: The rewrite that looks right and is not. Applied as an exact-string edit.
    wrong_edit: tuple[str, str]
    #: What a fluent model says about the wrong rewrite. Prose, not evidence.
    wrong_rationale: str
    #: The rewrite that is actually behaviour-preserving.
    right_edit: tuple[str, str]
    right_rationale: str
    #: Free-text note rendered under the divergence, naming the data at stake.
    stakes: str
    source_path: str = ""
    notes: list[str] = field(default_factory=list)

    def baseline_source(self) -> str:
        """Read the shipped VistA source for this routine."""
        path = repo_root() / (self.source_path or f"data/routines/{self.routine}.m")
        return path.read_text(encoding="utf-8")

    def _apply(self, source: str, edit: tuple[str, str]) -> str:
        old, new = edit
        if old not in source:
            raise ValueError(
                f"{self.task_id}: anchor line not found in {self.routine} source; "
                f"the corpus copy has changed under the task.\n  anchor: {old!r}"
            )
        return source.replace(old, new, 1)

    def wrong_candidate(self, baseline: str | None = None) -> str:
        return self._apply(baseline or self.baseline_source(), self.wrong_edit)

    def right_candidate(self, baseline: str | None = None) -> str:
        return self._apply(baseline or self.baseline_source(), self.right_edit)

    def specs(self) -> list[ExecSpec]:
        return [case.spec for case in self.cases]


def _nok_case(dfn: str, label: str, why: str) -> DemoCase:
    return DemoCase(
        spec=ExecSpec(
            routine="AJETIU2",
            entry="NOK",
            # rosetta.core passes args as VALUES, not M source expressions.
            args=[dfn, "NOK"],
            locals_in={"U": "^"},
            timeout_s=10.0,
        ),
        label=label,
        why=why,
    )


#: The money moment. See module docstring for why the ^DPT population matters.
NOK_TASK = DemoTask(
    task_id="nok-ajetiu2",
    routine="AJETIU2",
    entry="NOK",
    title="Guard the next-of-kin lookup in NOK^AJETIU2",
    request=(
        "NOK^AJETIU2 reads the next-of-kin node ^DPT(DFN,.21) and returns the "
        "next-of-kin name. Make the missing-record path explicit: when there is "
        "no next-of-kin record for the patient, the routine should say so "
        "rather than falling through. Do not change any other behaviour."
    ),
    cases=[
        _nok_case(
            "3",
            "DFN 3",
            "next-of-kin record present, name filled in -- the ordinary path",
        ),
        # 1043 VEHU patients have no .21 node at all. This case exists because
        # a model-written candidate passed the other four and changed the
        # answer for these: without it the suite says EQUIVALENT to a rewrite
        # that alters 1043 patients. A verifier is only as good as its inputs.
        _nok_case(
            "1",
            "DFN 1",
            "no ^DPT(1,.21) node at all -- the patient has no next-of-kin record",
        ),
        _nok_case(
            "2",
            "DFN 2",
            "no ^DPT(2,.21) node at all",
        ),
        _nok_case(
            "4",
            "DFN 4",
            "^DPT(4,.21) exists but every piece is empty -- no next-of-kin name",
        ),
        _nok_case(
            "86",
            "DFN 86",
            "^DPT(86,.21) is a short node, name piece blank",
        ),
        _nok_case(
            "88",
            "DFN 88",
            "^DPT(88,.21) present, name piece blank",
        ),
    ],
    wrong_edit=(
        ' S MRK=0 I NOK="" S NOK="Not Entered",MRK=1',
        ' S MRK=0 I $G(NA)="" S NOK="Not Entered",MRK=1',
    ),
    wrong_rationale=(
        "NA is fetched with $G, so it is safe, but NOK is derived from it before "
        "the emptiness test. Testing NA directly is the clearer expression of "
        "'there is no next-of-kin record', and it also protects the test from an "
        "undefined NA if the $G is ever removed. Behaviour is unchanged: when "
        "there is no record, NA is empty, so NOK is empty too."
    ),
    right_edit=(
        ' S MRK=0 I NOK="" S NOK="Not Entered",MRK=1',
        ' S MRK=0 I $P($G(NA),U)="" S NOK="Not Entered",MRK=1',
    ),
    right_rationale=(
        "The condition has to stay on the *name piece*, not on the whole node. "
        "Spelling it $P($G(NA),U) makes the missing-record path explicit without "
        "moving the test to a different value."
    ),
    stakes=(
        "^DPT(DFN,.21) is the next-of-kin node of the PATIENT file. In this "
        "database 765 patients have that node and 69 of them have it present "
        "with the name piece blank -- a next-of-kin record with an address and "
        "no name. For every one of those 69 the rewrite returns an empty string "
        "where the routine used to return 'Not Entered'."
    ),
    notes=[
        "NOK^AJETIU2 only reads globals, so there is no database divergence to "
        "find here; the whole signal is in the returned value.",
        "The DFN 1 / DFN 2 cases were added after a model-written candidate "
        "passed the other four by returning a new string for the 1043 patients "
        "with no .21 node. The verifier was right about the cases it was given; "
        "the cases were wrong.",
    ],
)


TASKS: dict[str, DemoTask] = {NOK_TASK.task_id: NOK_TASK}


def get_task(task_id: str) -> DemoTask:
    """Look up a demo task by id. Raises with the known ids on a miss."""
    try:
        return TASKS[task_id]
    except KeyError:
        raise KeyError(
            f"unknown demo task {task_id!r}; known: {sorted(TASKS)}"
        ) from None
