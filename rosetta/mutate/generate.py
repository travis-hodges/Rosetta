"""Task generation: mutant + input suite + admission, producing ``MutationTask``.

The pipeline, per docs/PROJECT.md sections 6 and 9:

1. Parse a known-good routine from ``data/routines/``.
2. Run the eight operators over it (:mod:`rosetta.mutate.operators`).
3. Build an input suite for the mutated line's entry point
   (:mod:`rosetta.mutate.cases`).
4. Admit the mutant only if all four validity rules hold:

   * **killable** -- ``verify_equivalence`` returns ``equivalent=False``
   * **compiles** -- the mutant loads (see :ref:`the caveat <compiles>` below)
   * **specific divergence** -- at least one named output or global differs; a
     bare timeout is recorded but flagged, not counted as a pass
   * **eval split** -- the routine is in the eval half of
     ``data/tasks/split.lock.json``

   Equivalent mutants are discarded, not downgraded. They are unkillable and
   would pollute the benchmark with unscoreable tasks.

.. _compiles:

**Compilation is checked by the verifier, not here.** Loading MUMPS source
requires YottaDB, which only ``rosetta.core`` may touch. This module applies
the structural guards in :mod:`rosetta.mutate.operators` and then relies on
``load_routine`` raising through ``verify_equivalence`` for anything they miss;
such a mutant is rejected with reason ``"load_failed"`` rather than admitted.

This module never writes ``data/tasks/split.lock.json``. It only reads it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Literal, Sequence

from rosetta.core.interface import ExecSpec, VerifyReport
from rosetta.mutate.cases import MIN_CASES, GlobalSampler, build_cases
from rosetta.mutate.lex import Routine, assert_no_tp_command, parse_routine
from rosetta.mutate.operators import Difficulty, Mutation, mutate_routine
from rosetta.mutate.verify import Verifier

__all__ = [
    "MutationTask",
    "Rejection",
    "GenerationResult",
    "generate_for_routine",
    "generate_tasks",
    "load_split",
    "task_to_dict",
    "write_tasks",
]


@dataclass(frozen=True)
class MutationTask:
    """One benchmark task: a routine with one injected regression.

    The first eight fields are exactly the shape specified in PROJECT.md
    section 6. Everything after them is an additive, defaulted extension --
    provenance the scorer and the report need, kept out of the specified
    constructor prefix so positional construction still matches the document.
    """

    task_id: str
    routine: str
    baseline_src: str  # correct original
    mutated_src: str  # what the agent is given
    operator: str
    line_no: int
    cases: list[ExecSpec]  # input suite that distinguishes them
    difficulty: Difficulty

    # --- provenance, beyond the section 6 shape ---
    detail: str = ""
    label: str | None = None
    entry: str | None = None
    original_line: str = ""
    mutated_line: str | None = ""
    n_diverged: int = 0
    divergence_kinds: tuple[str, ...] = ()
    timeout_only: bool = False
    timeout_risk: bool = False
    validated_by: str = ""
    note: str = ""


@dataclass(frozen=True)
class Rejection:
    """Why one candidate mutant did not become a task."""

    routine: str
    operator: str
    detail: str
    line_no: int
    reason: str


@dataclass
class GenerationResult:
    """Everything one generation run produced, admitted and rejected alike."""

    tasks: list[MutationTask] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)

    def reason_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rejections:
            out[r.reason] = out.get(r.reason, 0) + 1
        return out

    def by_operator(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for t in self.tasks:
            row = out.setdefault(t.operator, {"easy": 0, "medium": 0, "hard": 0})
            row[t.difficulty] += 1
        return out


# --------------------------------------------------------------------------
# Split lock (read-only)
# --------------------------------------------------------------------------


def load_split(path: str | Path) -> tuple[set[str], set[str]] | None:
    """Read ``data/tasks/split.lock.json`` and return ``(train, eval)`` sets.

    Returns ``None`` if the file does not exist yet -- it is written once,
    later, by the benchmark stream, and this module must never create it.
    Callers decide whether a missing split is fatal; :func:`generate_tasks`
    treats it as fatal unless ``require_split=False``.
    """
    p = Path(path)
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    return (
        {r.upper() for r in data.get("train", [])},
        {r.upper() for r in data.get("eval", [])},
    )


# --------------------------------------------------------------------------
# Task identity
# --------------------------------------------------------------------------


def _task_id(routine: str, mut: Mutation) -> str:
    digest = hashlib.sha256(mut.mutated_src.encode("utf-8")).hexdigest()[:8]
    return f"{routine}-{mut.operator}-L{mut.lineno}-{digest}"


def _entry_for(routine: Routine, mut: Mutation) -> str | None:
    """Pick the entry point whose span contains the mutated line.

    Prefers the containing label if it takes formal arguments, otherwise the
    nearest preceding label that does -- VistA entry points routinely delegate
    to an argumentless tag further down.
    """
    label = mut.label
    if label and routine.formals.get(label):
        return label
    best: str | None = None
    for name, (lo, _hi) in routine.label_spans.items():
        if lo <= mut.lineno and routine.formals.get(name):
            if best is None or routine.label_spans[best][0] < lo:
                best = name
    if best is not None:
        return best
    return label


# --------------------------------------------------------------------------
# Admission
# --------------------------------------------------------------------------


def _classify_report(report: VerifyReport) -> tuple[bool, tuple[str, ...]]:
    """``(has_specific_divergence, kinds)`` for one verify report.

    Section 9 rule 3: at least one *named* output or global must differ. An
    error code counts -- PROJECT.md section 14 is explicit that an M error is a
    valid divergence. A bare timeout does not; it is a weak signal and is
    flagged separately.
    """
    kinds = tuple(sorted({d.kind for d in report.divergences}))
    specific = any(d.kind in ("output", "global", "error") for d in report.divergences)
    return specific, kinds


def generate_for_routine(
    name: str,
    src: str,
    verifier: Verifier,
    *,
    operators: list[str] | None = None,
    sampler: GlobalSampler | None = None,
    n_cases: int = 10,
    max_tasks: int | None = None,
    allow_timeout_only: bool = False,
) -> GenerationResult:
    """Generate and validate every admissible task for one routine.

    ``verifier`` is required and is not defaulted: choosing an oracle is a
    decision the caller must make explicitly. See :mod:`rosetta.mutate.verify`.
    """
    result = GenerationResult()
    routine = parse_routine(name, src)
    assert_no_tp_command(src, f"baseline {name}")

    entry_cases: dict[str, list[ExecSpec]] = {}
    for mut in mutate_routine(routine, operators):
        if max_tasks is not None and len(result.tasks) >= max_tasks:
            break

        def reject(reason: str) -> None:
            result.rejections.append(
                Rejection(name, mut.operator, mut.detail, mut.lineno, reason)
            )

        entry = _entry_for(routine, mut)
        if entry is None:
            reject("no_entry_point")
            continue
        if entry not in entry_cases:
            try:
                entry_cases[entry] = build_cases(
                    routine, entry, sampler=sampler, n_cases=n_cases
                )
            except ValueError:
                entry_cases[entry] = []
        cases = entry_cases[entry]
        if len(cases) < MIN_CASES:
            reject("too_few_cases")
            continue

        assert_no_tp_command(mut.mutated_src, f"{name} {mut.key}")

        try:
            report = verifier.verify_equivalence(
                name, routine.src, mut.mutated_src, cases
            )
        except Exception as exc:  # noqa: BLE001 -- the reason matters, not the type
            # A load failure is the "it compiles" rule firing. Record it with
            # the message so a systematic generator bug is visible in the
            # rejection tally rather than swallowed.
            reject(f"load_failed:{type(exc).__name__}")
            continue

        if report.equivalent:
            reject("equivalent")  # unkillable; discard per section 9
            continue
        specific, kinds = _classify_report(report)
        if not specific:
            if not allow_timeout_only:
                reject("timeout_only")
                continue
        if report.n_cases and report.n_diverged == 0:
            reject("no_case_separates")
            continue

        result.tasks.append(
            MutationTask(
                task_id=_task_id(name, mut),
                routine=name.upper(),
                baseline_src=routine.src,
                mutated_src=mut.mutated_src,
                operator=mut.operator,
                line_no=mut.lineno,
                cases=cases,
                difficulty=mut.difficulty,
                detail=mut.detail,
                label=mut.label,
                entry=entry,
                original_line=mut.original_line,
                mutated_line=mut.mutated_line,
                n_diverged=report.n_diverged,
                divergence_kinds=kinds,
                timeout_only=not specific,
                timeout_risk=mut.timeout_risk,
                validated_by=getattr(verifier, "name", type(verifier).__name__),
                note=mut.note,
            )
        )
    return result


def generate_tasks(
    routines: Iterable[tuple[str, str]],
    verifier: Verifier,
    *,
    split_path: str | Path = "data/tasks/split.lock.json",
    require_split: bool = True,
    operators: list[str] | None = None,
    sampler: GlobalSampler | None = None,
    n_cases: int = 10,
    max_per_routine: int | None = None,
    allow_timeout_only: bool = False,
) -> GenerationResult:
    """Generate tasks across many routines, honouring the eval split.

    ``routines`` yields ``(name, source)``. Validity rule 4 -- "the routine is
    in the eval split" -- is enforced here: routines outside the eval list are
    rejected with reason ``"not_in_eval_split"``.

    ``require_split=True`` (the default) makes a missing ``split.lock.json`` a
    hard error. Building tasks without one is only defensible while the split
    does not exist yet, and the caller has to say so out loud. This function
    never writes the lock file.
    """
    split = load_split(split_path)
    if split is None and require_split:
        raise FileNotFoundError(
            f"{split_path} does not exist. It is written once by the benchmark "
            "stream and read by every generator; pass require_split=False only "
            "while it genuinely does not exist yet."
        )
    eval_set = split[1] if split else None

    out = GenerationResult()
    for name, src in routines:
        if eval_set is not None and name.upper() not in eval_set:
            out.rejections.append(Rejection(name, "-", "-", 0, "not_in_eval_split"))
            continue
        got = generate_for_routine(
            name,
            src,
            verifier,
            operators=operators,
            sampler=sampler,
            n_cases=n_cases,
            max_tasks=max_per_routine,
            allow_timeout_only=allow_timeout_only,
        )
        out.tasks.extend(got.tasks)
        out.rejections.extend(got.rejections)
    return out


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------


def task_to_dict(task: MutationTask) -> dict:
    """JSON-ready dict for one task. ``cases`` become plain ``ExecSpec`` dicts."""
    d = asdict(task)
    d["cases"] = [asdict(c) for c in task.cases]
    d["divergence_kinds"] = list(task.divergence_kinds)
    return d


def write_tasks(
    result: GenerationResult,
    path: str | Path,
    *,
    generator: str = "rosetta.mutate",
    extra: dict | None = None,
) -> Path:
    """Write a task file with its rejection tally, and return the path.

    The rejection tally is part of the artifact on purpose: "how many mutants
    were discarded as equivalent" is a number a skeptical reader will ask for,
    and it must come from a file rather than a claim.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema": "rosetta.mutate.tasks/1",
        "generator": generator,
        "validated_by": sorted({t.validated_by for t in result.tasks}),
        "counts": {
            "tasks": len(result.tasks),
            "rejected": len(result.rejections),
            "by_operator": result.by_operator(),
            "by_reason": result.reason_counts(),
        },
        "tasks": [task_to_dict(t) for t in result.tasks],
    }
    if extra:
        doc.update(extra)
    p.write_text(json.dumps(doc, indent=1))
    return p
