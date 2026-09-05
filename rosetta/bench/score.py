"""Scoring: turn ``results/*.jsonl`` traces into the section 9 metrics.

Every number the website publishes is computed here, from trace records and
nothing else. There are no defaults, no fallbacks and no hand-entered figures:
if the traces do not contain an attempt, the task is not scored and the
exclusion is counted and reported.

Metric definitions, stated once and precisely
=============================================

**Scoreable attempt.** An attempt is scoreable when the harness did not fail,
the harness graded it with ``verify_equivalence`` after the fact, and the
grading verdict had at least one non-void case. Void cases "carry NO
information and must never be scored" (frozen contract), so a verdict that is
entirely void is not a verdict.

**Scored task.** A task counts for a condition only when its **attempt 1** is
scoreable. Attempt 1 is the delivered answer; a condition that could not produce
one has no number for that task, and quietly promoting attempt 2 into its place
would flatter the result. Excluded tasks are counted by reason.

**pass@1** -- fraction of scored tasks whose attempt 1 verified equivalent.
Headline metric.

**pass@k** -- fraction of scored tasks where any of attempts ``1..k`` verified.
Reported only when *every* scored task has at least ``k`` scoreable attempts;
otherwise ``None``, because a partial denominator is not a pass rate.

**mean repair iterations** -- over tasks that passed, the mean number of
verifier tool calls (``verify_change``, ``run_task_cases``) made during the
first passing attempt. This is "verifier calls before success" from section 9.
Calls made in earlier, failed attempts are not counted: attempts are
independent samples, and mixing them would measure attempt count, not loop
depth. Zero by construction in the tools-off baseline, where it is reported as
``None`` rather than as a misleading 0.0.

**per-operator pass rate** -- pass@1 restricted to each mutation operator.

**false-confidence rate** -- section 9 calls this the most important number in
the project. Definition used here:

    false_confidence_rate =
        (tasks where attempt 1 asserted correctness AND did not verify)
        / (all scored tasks)

Two judgements are baked into that, and both are deliberate:

*Denominator.* All scored tasks, the same denominator as pass@1. That makes the
two directly comparable -- ``false_confidence_rate <= 1 - pass_at_1`` always
holds, so the bar reads as "the share of the run that came back confidently
wrong", and the site can plot them on one axis honestly. The alternative
denominator, "tasks where the model asserted correctness", answers a different
and also useful question -- when it claims success, how often is it lying? --
and is reported as :attr:`ConditionScore.false_confidence_given_assertion`
alongside :attr:`assertion_rate`, so the reader can see both without either
being able to hide the other.

*What counts as an assertion.* Recorded mechanically by the harness, never
inferred from prose -- see :class:`rosetta.bench.trace.Assertion`. In short: an
explicit structured claim if the harness captured one, otherwise submitting a
final candidate as the answer counts as asserting it is correct, and an attempt
that submitted nothing does not count as an assertion at all (it still fails
pass@1).

**tool disagreements** -- attempts where the model's own last ``verify_change``
said equivalent but the grading verdict said otherwise. Not a published metric;
it is a self-check on the verifier and the case suites, and a non-zero value
needs an explanation before anything is published.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence

from rosetta.bench.trace import CONDITIONS, TraceRecord

__all__ = [
    "ConditionScore",
    "OperatorScore",
    "RunScore",
    "ScoringError",
    "TaskAttempts",
    "group_attempts",
    "score_condition",
    "score_run",
]


class ScoringError(RuntimeError):
    """The traces cannot be scored as given. Raised rather than guessed around."""


@dataclass(frozen=True)
class OperatorScore:
    """pass@1 restricted to one mutation operator."""

    operator: str
    n_tasks: int
    n_passed: int
    n_false_confident: int

    @property
    def pass_at_1(self) -> float:
        return self.n_passed / self.n_tasks if self.n_tasks else 0.0

    @property
    def false_confidence_rate(self) -> float:
        return self.n_false_confident / self.n_tasks if self.n_tasks else 0.0

    def to_json(self) -> dict[str, object]:
        return {
            "operator": self.operator,
            "n_tasks": self.n_tasks,
            "n_passed": self.n_passed,
            "pass_at_1": self.pass_at_1,
            "n_false_confident": self.n_false_confident,
            "false_confidence_rate": self.false_confidence_rate,
        }


@dataclass(frozen=True)
class TaskAttempts:
    """Every attempt at one task under one condition, ordered by attempt number."""

    task_id: str
    condition: str
    attempts: tuple[TraceRecord, ...]

    @property
    def first(self) -> TraceRecord | None:
        for rec in self.attempts:
            if rec.attempt == 1:
                return rec
        return None

    @property
    def scoreable_attempts(self) -> tuple[TraceRecord, ...]:
        return tuple(r for r in self.attempts if r.scoreable)

    def first_pass(self) -> TraceRecord | None:
        for rec in self.scoreable_attempts:
            if rec.passed:
                return rec
        return None

    def passed_within(self, k: int) -> bool:
        return any(r.passed for r in self.scoreable_attempts if r.attempt <= k)

    @property
    def operator(self) -> str:
        for rec in self.attempts:
            if rec.operator:
                return rec.operator
        return "UNKNOWN"

    @property
    def difficulty(self) -> str:
        for rec in self.attempts:
            if rec.difficulty:
                return rec.difficulty
        return "unknown"


@dataclass(frozen=True)
class ConditionScore:
    """All section 9 metrics for one condition."""

    condition: str
    n_tasks: int
    n_passed: int
    n_asserted: int
    n_false_confident: int
    pass_at_k: Mapping[int, float | None]
    max_attempts: int
    min_attempts: int
    repair_iterations: tuple[int, ...]
    tools_used: bool
    tool_disagreements: int
    excluded: Mapping[str, int]
    excluded_task_ids: tuple[str, ...]
    models: tuple[str, ...]
    run_ids: tuple[str, ...]
    per_operator: Mapping[str, OperatorScore]
    per_difficulty: Mapping[str, OperatorScore]

    # -- headline ------------------------------------------------------

    @property
    def pass_at_1(self) -> float:
        value = self.pass_at_k.get(1)
        if value is None:
            raise ScoringError(f"{self.condition}: pass@1 is undefined with 0 tasks")
        return value

    @property
    def pass_at_3(self) -> float | None:
        return self.pass_at_k.get(3)

    @property
    def false_confidence_rate(self) -> float:
        """Confidently-wrong share of all scored tasks. See the module docstring."""
        return self.n_false_confident / self.n_tasks if self.n_tasks else 0.0

    @property
    def assertion_rate(self) -> float:
        """Share of scored tasks where the model asserted correctness at all."""
        return self.n_asserted / self.n_tasks if self.n_tasks else 0.0

    @property
    def false_confidence_given_assertion(self) -> float | None:
        """Of the answers it claimed were correct, the share that were not."""
        if not self.n_asserted:
            return None
        return self.n_false_confident / self.n_asserted

    @property
    def mean_repair_iterations(self) -> float | None:
        """Mean verifier calls in the first passing attempt. ``None`` tools-off."""
        if not self.tools_used or not self.repair_iterations:
            return None
        return statistics.fmean(self.repair_iterations)

    @property
    def median_repair_iterations(self) -> float | None:
        if not self.tools_used or not self.repair_iterations:
            return None
        return statistics.median(self.repair_iterations)

    def to_json(self) -> dict[str, object]:
        return {
            "condition": self.condition,
            "n_tasks": self.n_tasks,
            "n_passed": self.n_passed,
            "pass_at_1": self.pass_at_1 if self.n_tasks else None,
            "pass_at_3": self.pass_at_3,
            "pass_at_k": {str(k): v for k, v in sorted(self.pass_at_k.items())},
            "attempts_per_task": {"min": self.min_attempts, "max": self.max_attempts},
            "n_asserted": self.n_asserted,
            "assertion_rate": self.assertion_rate,
            "n_false_confident": self.n_false_confident,
            "false_confidence_rate": self.false_confidence_rate,
            "false_confidence_given_assertion": self.false_confidence_given_assertion,
            "tools_used": self.tools_used,
            "mean_repair_iterations": self.mean_repair_iterations,
            "median_repair_iterations": self.median_repair_iterations,
            "repair_iterations": list(self.repair_iterations),
            "tool_disagreements": self.tool_disagreements,
            "excluded": dict(sorted(self.excluded.items())),
            "excluded_task_ids": list(self.excluded_task_ids),
            "models": list(self.models),
            "run_ids": list(self.run_ids),
            "per_operator": {
                k: v.to_json() for k, v in sorted(self.per_operator.items())
            },
            "per_difficulty": {
                k: v.to_json() for k, v in sorted(self.per_difficulty.items())
            },
        }


@dataclass(frozen=True)
class RunScore:
    """Every condition present in the traces, plus the comparable task set."""

    conditions: Mapping[str, ConditionScore]
    #: Tasks scoreable in *every* condition. The published figures use these so
    #: the bars compare like with like.
    common_task_ids: tuple[str, ...]
    n_records: int
    duplicate_attempts: tuple[str, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "n_records": self.n_records,
            "n_common_tasks": len(self.common_task_ids),
            "common_task_ids": list(self.common_task_ids),
            "duplicate_attempts": list(self.duplicate_attempts),
            "conditions": {
                cid: self.conditions[cid].to_json()
                for cid in CONDITIONS
                if cid in self.conditions
            },
        }


# ----------------------------------------------------------------------
# Grouping
# ----------------------------------------------------------------------


def group_attempts(
    records: Iterable[TraceRecord],
) -> tuple[dict[str, dict[str, TaskAttempts]], list[str]]:
    """Group records by condition and task. Returns ``(grouped, duplicates)``.

    A duplicate is the same ``(condition, task_id, attempt)`` appearing twice.
    Duplicates are reported, not merged -- two verdicts for one attempt means
    the producer is broken and the caller must decide, not the scorer.
    """
    buckets: dict[tuple[str, str], list[TraceRecord]] = {}
    seen: set[tuple[str, str, int]] = set()
    duplicates: list[str] = []
    for rec in records:
        key = (rec.condition, rec.task_id)
        triple = (rec.condition, rec.task_id, rec.attempt)
        if triple in seen:
            duplicates.append(f"{rec.condition}/{rec.task_id}#{rec.attempt}")
        seen.add(triple)
        buckets.setdefault(key, []).append(rec)

    grouped: dict[str, dict[str, TaskAttempts]] = {}
    for (condition, task_id), recs in buckets.items():
        recs.sort(key=lambda r: r.attempt)
        grouped.setdefault(condition, {})[task_id] = TaskAttempts(
            task_id=task_id, condition=condition, attempts=tuple(recs)
        )
    return grouped, duplicates


# ----------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------


def score_condition(
    condition: str,
    tasks: Mapping[str, TaskAttempts],
    *,
    task_ids: Sequence[str] | set[str] | None = None,
    ks: Sequence[int] = (1, 3),
) -> ConditionScore:
    """Score one condition over its tasks, optionally restricted to ``task_ids``."""
    wanted = set(task_ids) if task_ids is not None else set(tasks)

    scored: list[TaskAttempts] = []
    excluded: dict[str, int] = {}
    excluded_ids: list[str] = []

    def drop(task_id: str, reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1
        excluded_ids.append(f"{task_id}:{reason}")

    for task_id in sorted(wanted):
        entry = tasks.get(task_id)
        if entry is None:
            drop(task_id, "no_attempts")
            continue
        first = entry.first
        if first is None:
            drop(task_id, "no_attempt_1")
            continue
        if not first.scoreable:
            drop(task_id, first.exclusion_reason or "unscoreable")
            continue
        scored.append(entry)

    n_tasks = len(scored)
    n_passed = sum(1 for t in scored if (t.first is not None and t.first.passed))
    n_asserted = sum(
        1 for t in scored if (t.first is not None and t.first.asserted_correct)
    )
    n_false_confident = sum(
        1
        for t in scored
        if t.first is not None and t.first.asserted_correct and not t.first.passed
    )

    attempt_counts = [len(t.scoreable_attempts) for t in scored] or [0]
    pass_at_k: dict[int, float | None] = {}
    for k in ks:
        if not n_tasks:
            pass_at_k[k] = None
        elif min(attempt_counts) < k:
            # Not every task has k independent samples; a pass@k over a mixed
            # denominator is not a pass@k.
            pass_at_k[k] = None
        else:
            pass_at_k[k] = sum(1 for t in scored if t.passed_within(k)) / n_tasks
    if n_tasks:
        pass_at_k[1] = n_passed / n_tasks

    tools_used = any(
        rec.verifier_call_count > 0 for t in scored for rec in t.scoreable_attempts
    )
    repair_iterations: list[int] = []
    for t in scored:
        winner = t.first_pass()
        if winner is not None:
            repair_iterations.append(winner.verifier_call_count)

    tool_disagreements = sum(
        1
        for t in scored
        for rec in t.scoreable_attempts
        if rec.tool_said_equivalent and not rec.passed
    )

    per_operator = _breakdown(scored, key=lambda t: t.operator)
    per_difficulty = _breakdown(scored, key=lambda t: t.difficulty)

    models = sorted({rec.model for t in scored for rec in t.attempts if rec.model})
    run_ids = sorted({rec.run_id for t in scored for rec in t.attempts if rec.run_id})

    return ConditionScore(
        condition=condition,
        n_tasks=n_tasks,
        n_passed=n_passed,
        n_asserted=n_asserted,
        n_false_confident=n_false_confident,
        pass_at_k=pass_at_k,
        max_attempts=max(attempt_counts),
        min_attempts=min(attempt_counts),
        repair_iterations=tuple(repair_iterations),
        tools_used=tools_used,
        tool_disagreements=tool_disagreements,
        excluded=excluded,
        excluded_task_ids=tuple(excluded_ids),
        models=tuple(models),
        run_ids=tuple(run_ids),
        per_operator=per_operator,
        per_difficulty=per_difficulty,
    )


def _breakdown(
    scored: Sequence[TaskAttempts], key: Callable[[TaskAttempts], str]
) -> dict[str, OperatorScore]:
    groups: dict[str, list[TaskAttempts]] = {}
    for t in scored:
        groups.setdefault(str(key(t)), []).append(t)
    out: dict[str, OperatorScore] = {}
    for name, members in groups.items():
        firsts = [m.first for m in members if m.first is not None]
        out[name] = OperatorScore(
            operator=name,
            n_tasks=len(firsts),
            n_passed=sum(1 for f in firsts if f.passed),
            n_false_confident=sum(
                1 for f in firsts if f.asserted_correct and not f.passed
            ),
        )
    return out


def score_run(
    records: Iterable[TraceRecord],
    *,
    task_ids: Sequence[str] | set[str] | None = None,
    ks: Sequence[int] = (1, 3),
    common_only: bool = False,
) -> RunScore:
    """Score every condition present in ``records``.

    ``task_ids`` restricts scoring to a known task set (the scorer's defence
    against a trace referring to a task that is not in the built benchmark).
    ``common_only`` further restricts every condition to the tasks scoreable in
    all of them, which is what :mod:`rosetta.bench.report` publishes.
    """
    records = list(records)
    grouped, duplicates = group_attempts(records)
    if not grouped:
        raise ScoringError("no trace records to score")

    unknown = sorted(set(grouped) - set(CONDITIONS))
    if unknown:  # pragma: no cover -- TraceRecord.from_json already rejects these
        raise ScoringError(f"unknown conditions in traces: {unknown}")

    first_pass_scores = {
        cid: score_condition(cid, tasks, task_ids=task_ids, ks=ks)
        for cid, tasks in grouped.items()
    }

    scoreable_sets = []
    for cid, tasks in grouped.items():
        wanted = set(task_ids) if task_ids is not None else set(tasks)
        scoreable_sets.append(
            {
                tid
                for tid in wanted
                if (entry := tasks.get(tid)) is not None
                and entry.first is not None
                and entry.first.scoreable
            }
        )
    common = sorted(set.intersection(*scoreable_sets)) if scoreable_sets else []

    conditions = first_pass_scores
    if common_only:
        conditions = {
            cid: score_condition(cid, tasks, task_ids=common, ks=ks)
            for cid, tasks in grouped.items()
        }

    return RunScore(
        conditions=conditions,
        common_task_ids=tuple(common),
        n_records=len(records),
        duplicate_attempts=tuple(sorted(duplicates)),
    )
