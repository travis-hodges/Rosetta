"""Emit ``results/summary.json`` (the website contract) and the human view.

Two artifacts, one source of truth:

``results/summary.json``
    The published benchmark contract, defined here and enforced by
    :func:`validate_summary`. Nothing else goes in it -- every extra field is a
    chance to break that contract for whatever consumes the report next.

``results/report.json`` + a text table
    Per-operator breakdown, bounded repair success, assertion rates,
    exclusions, and provenance. Feedback retries are not independent pass@k.

Rules this module enforces, all of which fail loudly:

* Numbers come from traces. There is no way to pass a figure in by hand.
* The published ``split_hash`` is read from ``data/tasks/split.lock.json``.
  Results are inseparable from the split they were measured against.
* Every scored task must belong to the built task set, and its routine must be
  on the eval side of the lock. A trace naming a train-split routine stops the
  report rather than quietly inflating it.
* ``baseline`` and ``scaffolded`` must both be present, with a non-empty set of
  tasks scoreable in every condition. Otherwise nothing is written and the site
  keeps showing pending -- which is the honest state.

Usage::

    python -m rosetta.bench.report --tasks data/tasks/eval_tasks.json
    python -m rosetta.bench.report --traces results/bench --print-only

Benchmark traces are read from ``results/bench/*.jsonl``. ``results/`` also
carries the demo harness's event streams, which are a different producer with a
different record shape; the two are kept in separate directories so neither
reader has to guess what it is looking at.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from rosetta.bench import build as build_mod
from rosetta.bench import split as split_mod
from rosetta.bench.score import ConditionScore, RunScore, score_run
from rosetta.bench.trace import CONDITIONS, PROTOCOL, TraceRecord, read_traces, task_fingerprint

__all__ = [
    "REQUIRED_CONDITIONS",
    "SUMMARY_SCHEMA_VERSION",
    "ReportRefused",
    "Report",
    "build_report",
    "main",
    "render_text",
    "validate_summary",
    "write_report",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS = REPO_ROOT / "results"
#: Benchmark traces live in their own directory. ``results/`` itself also holds
#: the demo harness's event streams, which are a different producer with a
#: different shape; keeping them apart means neither reader has to guess.
DEFAULT_TRACES = DEFAULT_RESULTS / "bench"
DEFAULT_SUMMARY = DEFAULT_RESULTS / "summary.json"
DEFAULT_DETAIL = DEFAULT_RESULTS / "report.json"

SUMMARY_SCHEMA_VERSION = 1
REQUIRED_CONDITIONS = ("baseline", "scaffolded")
_HEX64 = re.compile(r"^[a-f0-9]{64}$", re.IGNORECASE)


class ReportRefused(RuntimeError):
    """The run cannot be published. Nothing is written."""


# ----------------------------------------------------------------------
# The published schema
# ----------------------------------------------------------------------


def validate_summary(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The published contract for ``summary.json``. Raises on anything it rejects.

    Deliberately literal: a producer that drifts from this is a test failure
    rather than a malformed report discovered by whoever reads it.
    """
    if doc.get("schema_version") != SUMMARY_SCHEMA_VERSION:
        raise ReportRefused("Expected a schema_version 1 benchmark report.")
    generated_at = doc.get("generated_at")
    if not isinstance(generated_at, str) or not _parses_as_date(generated_at):
        raise ReportRefused("The report needs a valid generated_at date.")
    task_count = doc.get("task_count")
    if not isinstance(task_count, int) or isinstance(task_count, bool) or task_count < 1:
        raise ReportRefused("The report needs a positive task_count.")
    split_hash = doc.get("split_hash")
    if not isinstance(split_hash, str) or not _HEX64.match(split_hash):
        raise ReportRefused("The report needs the SHA-256 hash of the locked split.")
    conditions = doc.get("conditions")
    if not isinstance(conditions, list) or not 2 <= len(conditions) <= 3:
        raise ReportRefused("Provide two or three measured conditions.")
    seen: set[str] = set()
    for cond in conditions:
        cid = cond.get("id") if isinstance(cond, Mapping) else None
        if cid not in CONDITIONS or cid in seen:
            raise ReportRefused(
                "Condition identifiers are missing, duplicated, or unsupported."
            )
        seen.add(cid)
        for metric in ("pass_at_1", "false_confidence_rate"):
            value = cond.get(metric)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value != value  # NaN
                or value in (float("inf"), float("-inf"))
                or not 0 <= value <= 1
            ):
                raise ReportRefused("Rates must be finite numbers between 0 and 1.")
    missing = [c for c in REQUIRED_CONDITIONS if c not in seen]
    if missing:
        raise ReportRefused(f"Both baseline and scaffolded conditions are required: {missing}")
    return dict(doc)


def _parses_as_date(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


# ----------------------------------------------------------------------
# Building
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Report:
    """The pair of documents, held together so they can only agree."""

    summary: dict[str, Any]
    detail: dict[str, Any]
    score: RunScore

    @property
    def task_count(self) -> int:
        return int(self.summary["task_count"])


def _condition_row(score: ConditionScore) -> dict[str, Any]:
    return {
        "id": score.condition,
        "pass_at_1": score.pass_at_1,
        "false_confidence_rate": score.false_confidence_rate,
    }


def build_report(
    records: Iterable[TraceRecord],
    *,
    lock_path: Path = split_mod.DEFAULT_LOCK,
    task_set: Mapping[str, Any] | None = None,
    generated_at: str | None = None,
) -> Report:
    """Score the traces and assemble both documents. Publishes nothing."""
    records = list(records)
    if not records:
        raise ReportRefused("no trace records; nothing measured, nothing published")

    protocols = {r.extra.get("protocol") for r in records}
    if protocols != {PROTOCOL}:
        raise ReportRefused(
            "legacy, unknown, or mixed benchmark protocols cannot be published; "
            "rerun with isolated candidate generation and recorded protocol provenance"
        )
    run_ids = {r.run_id for r in records}
    if len(run_ids) != 1 or not next(iter(run_ids)):
        raise ReportRefused("compare conditions from one identified run, not mixed runs")
    budgets = {r.extra.get("attempt_budget") for r in records}
    timeouts = {r.extra.get("model_timeout_s") for r in records}
    if (len(budgets) != 1 or any(type(b) is not int or b < 1 for b in budgets)
            or len(timeouts) != 1 or any(type(t) not in (int, float)
                                       or not math.isfinite(t) or t <= 0
                                       for t in timeouts)):
        raise ReportRefused("all conditions need the same positive attempt and timeout budgets")
    if any(r.extra.get("agent_tools_enabled") is not False for r in records):
        raise ReportRefused("this protocol requires candidate tools to be disabled")
    models = {c: {r.model for r in records if r.condition == c}
              for c in {r.condition for r in records}}
    if any(len(v) != 1 or not next(iter(v)) or next(iter(v)) == "opencode"
           for v in models.values()):
        raise ReportRefused("each condition needs one explicit provider/model identity")
    if models.get("baseline") != models.get("scaffolded"):
        raise ReportRefused("baseline and scaffolded must use the same model")
    fingerprints: dict[str, str] = {}
    for record in records:
        fingerprint = record.extra.get("task_sha256", "")
        if not isinstance(fingerprint, str) or not _HEX64.fullmatch(fingerprint):
            raise ReportRefused("every trace needs an exact task fingerprint")
        if fingerprints.setdefault(record.task_id, fingerprint) != fingerprint:
            raise ReportRefused(f"task content differs between attempts: {record.task_id}")

    lock = split_mod.load_lock(lock_path)
    split_hash = str(lock["content_hash"])
    eval_set = {str(n).upper() for n in lock["eval"]}
    train_set = {str(n).upper() for n in lock["train"]}

    known_task_ids: set[str] | None = None
    task_routine: dict[str, str] = {}
    if task_set is not None:
        if task_set.get("schema") != build_mod.SCHEMA:
            raise ReportRefused(
                f"task set schema {task_set.get('schema')!r} is not {build_mod.SCHEMA}"
            )
        prov = task_set.get("provenance", {})
        if str(prov.get("split_hash")) != split_hash:
            raise ReportRefused(
                "the task set was built against a different split than the lock "
                f"({prov.get('split_hash')} vs {split_hash})"
            )
        if not prov.get("publishable", False):
            raise ReportRefused(
                "the task set is stamped unpublishable (tasks were not validated "
                "by rosetta.core); refusing to publish numbers measured on it"
            )
        known_task_ids = {str(t["task_id"]) for t in task_set.get("tasks", [])}
        task_routine = {
            str(t["task_id"]): str(t["routine"]).upper()
            for t in task_set.get("tasks", [])
        }
        for task in task_set.get("tasks", []):
            tid = str(task["task_id"])
            expected = task_set.get("routine_sources", {}).get(task["routine"])
            if expected and hashlib.sha256(task.get("baseline_src", "").encode()).hexdigest() != expected:
                raise ReportRefused(f"task reference differs from source manifest: {tid}")
            if tid in fingerprints and all(key in task for key in
                    ("baseline_src", "mutated_src", "cases")):
                if fingerprints[tid] != task_fingerprint(task):
                    raise ReportRefused(f"trace task fingerprint differs from task set: {tid}")
        if any(r.task_id in task_routine and r.routine
               and r.routine.upper() != task_routine[r.task_id] for r in records):
            raise ReportRefused("trace routine does not match its task set (possible train-split mismatch)")
        # The task set is authoritative for task facts, so a producer that left
        # routine/operator/difficulty blank still gets a per-operator breakdown.
        facts = {
            str(t["task_id"]): (
                str(t["routine"]).upper(),
                str(t.get("operator", "")),
                str(t.get("difficulty", "")),
            )
            for t in task_set.get("tasks", [])
        }
        records = [
            r.with_task_metadata(*facts[r.task_id]) if r.task_id in facts else r
            for r in records
        ]

    # Leakage and membership checks, before any number is computed.
    unknown_tasks = sorted(
        {
            r.task_id
            for r in records
            if known_task_ids is not None and r.task_id not in known_task_ids
        }
    )
    if unknown_tasks:
        raise ReportRefused(
            f"{len(unknown_tasks)} task ids in the traces are not in the built task "
            f"set, e.g. {unknown_tasks[:3]}. Score only what was built."
        )
    leaked = sorted(
        {
            (r.routine or task_routine.get(r.task_id, "")).upper()
            for r in records
            if (r.routine or task_routine.get(r.task_id, "")).upper() in train_set
        }
    )
    if leaked:
        raise ReportRefused(
            f"traces reference train-split routines: {leaked}. "
            "Published numbers must come from the eval side only."
        )
    off_split = sorted(
        {
            routine
            for r in records
            if (routine := (r.routine or task_routine.get(r.task_id, "")).upper())
            and routine not in eval_set
        }
    )
    if off_split:
        raise ReportRefused(
            f"traces reference routines outside the split lock: {off_split[:5]}"
        )

    all_conditions = score_run(records, task_ids=known_task_ids, common_only=False)
    score = score_run(records, task_ids=known_task_ids, common_only=True)

    missing = [c for c in REQUIRED_CONDITIONS if c not in score.conditions]
    if missing:
        raise ReportRefused(
            f"missing required condition(s) {missing}; the site needs baseline and "
            "scaffolded. Nothing published -- the pending state is the honest one."
        )
    if len(score.conditions) > 3:
        raise ReportRefused(f"too many conditions: {sorted(score.conditions)}")
    if not score.common_task_ids:
        raise ReportRefused(
            "no task is scoreable in every condition; there is nothing comparable "
            "to publish. Per-condition exclusions: "
            + json.dumps(
                {c: dict(s.excluded) for c, s in all_conditions.conditions.items()}, sort_keys=True
            )
        )
    if score.duplicate_attempts:
        raise ReportRefused(
            "duplicate (condition, task, attempt) records in the traces: "
            f"{list(score.duplicate_attempts)[:5]}"
        )

    stamp = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    ordered = [c for c in CONDITIONS if c in score.conditions]
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "generated_at": stamp,
        "task_count": len(score.common_task_ids),
        "split_hash": split_hash,
        "conditions": [_condition_row(score.conditions[c]) for c in ordered],
    }
    validate_summary(summary)

    bounded_attempts = {}
    budget = next(iter(budgets))
    for condition in ordered:
        successes = []
        incomplete = []
        for tid in score.common_task_ids:
            attempts = [r for r in records if r.condition == condition and r.task_id == tid]
            passing = [r.attempt for r in attempts if r.passed and r.attempt <= budget]
            if passing:
                successes.append(min(passing))
            elif {r.attempt for r in attempts if r.scoreable} != set(range(1, budget + 1)):
                incomplete.append(tid)
        bounded_attempts[condition] = {
            "attempt_budget": budget,
            "n_tasks": len(score.common_task_ids),
            "n_passed": len(successes),
            "incomplete_task_ids": incomplete,
            "success_rate": None if incomplete else len(successes) / len(score.common_task_ids),
            "mean_attempts_to_success": sum(successes) / len(successes) if successes else None,
        }

    run_detail = score.to_json()
    for condition in run_detail["conditions"].values():
        # The generic scorer can read historical independent-sample traces;
        # this protocol's sequential feedback retries do not justify pass@k.
        condition.pop("pass_at_3", None)
        condition.pop("pass_at_k", None)

    detail = {
        "schema": "rosetta.bench.report/1",
        "generated_at": stamp,
        "summary": summary,
        "protocol": {
            "id": PROTOCOL,
            "run_id": next(iter(run_ids)),
            "models": {c: next(iter(v)) for c, v in models.items()},
            "attempt_budget": next(iter(budgets)),
            "model_timeout_s": next(iter(timeouts)),
            "agent_tools_enabled": False,
            "comparison": "no feedback versus harness feedback; not autonomous MCP tool use",
            "task_sha256": fingerprints,
        },
        "coverage": {
            "task_set_count": len(known_task_ids) if known_task_ids is not None else None,
            "attempted_task_count": len({r.task_id for r in records}),
            "common_scoreable_task_count": len(score.common_task_ids),
            "partial": known_task_ids is None or set(score.common_task_ids) != known_task_ids,
            "condition_exclusions": {
                c: {"reasons": dict(s.excluded), "task_exclusions": list(s.excluded_task_ids)}
                for c, s in all_conditions.conditions.items()
            },
        },
        "bounded_attempts": bounded_attempts,
        "split": {
            "hash": split_hash,
            "lock": str(lock_path),
            "counts": lock.get("counts"),
        },
        "task_set": (
            None
            if task_set is None
            else {
                "provenance": task_set.get("provenance"),
                "counts": task_set.get("counts"),
            }
        ),
        "scoring": {
            "false_confidence_rate": (
                "tasks where attempt 1 asserted correctness and did not verify, "
                "over all scored tasks"
            ),
            "assertion": (
                "explicit harness-recorded claim when present; otherwise submitting "
                "a final candidate counts as asserting it is correct; an attempt "
                "with no submission is not an assertion"
            ),
            "repair_iterations": (
                "verifier tool calls during the first passing attempt, "
                "averaged over tasks that passed"
            ),
            "scored_task": "a task whose attempt 1 was graded and not void",
            "published_tasks": "tasks scoreable in every condition",
            "attempts": (
                "baseline retries are independent; scaffolded retries receive harness feedback. "
                "A multi-attempt success rate is a bounded repair result, not independent pass@k"
            ),
        },
        "run": run_detail,
    }
    return Report(summary=summary, detail=detail, score=score)


def write_report(
    report: Report,
    *,
    summary_path: Path = DEFAULT_SUMMARY,
    detail_path: Path | None = DEFAULT_DETAIL,
) -> list[Path]:
    """Write the published summary and the detail view. Validates once more first."""
    validate_summary(report.summary)
    written: list[Path] = []
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(report.summary, indent=2) + "\n")
    written.append(summary_path)
    if detail_path is not None:
        detail_path.parent.mkdir(parents=True, exist_ok=True)
        detail_path.write_text(json.dumps(report.detail, indent=1) + "\n")
        written.append(detail_path)
    return written


# ----------------------------------------------------------------------
# Human view
# ----------------------------------------------------------------------

_NAMES = {
    "baseline": "No feedback",
    "scaffolded": "Harness feedback",
    "tuned_scaffolded": "Tuned + feedback",
}


def _pct(value: float | None) -> str:
    return "     -" if value is None else f"{value * 100:5.1f}%"


def render_text(report: Report) -> str:
    """The table a human reads before anything is published."""
    score = report.score
    lines: list[str] = []
    lines.append(
        f"Rosetta benchmark  {report.summary['generated_at']}  "
        f"split {report.summary['split_hash'][:12]}  "
        f"{report.task_count} tasks scored in every condition"
    )
    lines.append("")
    coverage = report.detail["coverage"]
    if coverage["partial"]:
        lines.append(f"PARTIAL evidence: {coverage['common_scoreable_task_count']} common scoreable "
                     f"tasks of {coverage['task_set_count'] or 'unknown'} in the task set.")
    lines.append("Protocol: isolated generation; scaffolded receives harness feedback, not MCP tools.")
    lines.append("Repair column: success within the attempt budget; mean tries is over successes.")
    lines.append("")
    repair_label = f"repair≤{report.detail['protocol']['attempt_budget']}"
    header = (
        f"{'condition':<22}{'pass@1':>8}{repair_label:>10}{'false conf':>12}"
        f"{'assert':>8}{'FC|assert':>11}{'mean try':>8}{'n':>6}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for cid in CONDITIONS:
        s = score.conditions.get(cid)
        if s is None:
            continue
        bounded = report.detail["bounded_attempts"][cid]
        repair = bounded["mean_attempts_to_success"]
        lines.append(
            f"{_NAMES.get(cid, cid):<22}"
            f"{_pct(s.pass_at_1):>8}"
            f"{_pct(bounded['success_rate']):>10}"
            f"{_pct(s.false_confidence_rate):>12}"
            f"{_pct(s.assertion_rate):>8}"
            f"{_pct(s.false_confidence_given_assertion):>11}"
            f"{'       -' if repair is None else f'{repair:8.2f}'}"
            f"{s.n_tasks:>6}"
        )

    lines.append("")
    lines.append("per operator (pass@1 / false confidence, attempt 1)")
    operators = sorted(
        {op for s in score.conditions.values() for op in s.per_operator}
    )
    cond_ids = [c for c in CONDITIONS if c in score.conditions]
    lines.append(
        f"{'operator':<16}{'n':>5}" + "".join(f"{_NAMES.get(c, c):>26}" for c in cond_ids)
    )
    for op in operators:
        counts = {c: score.conditions[c].per_operator.get(op) for c in cond_ids}
        n = max((v.n_tasks for v in counts.values() if v), default=0)
        cells = ""
        for c in cond_ids:
            v = counts[c]
            cells += (
                f"{'':>26}"
                if v is None
                else f"{_pct(v.pass_at_1)} / {_pct(v.false_confidence_rate)}".rjust(26)
            )
        lines.append(f"{op:<16}{n:>5}{cells}")

    lines.append("")
    for cid in cond_ids:
        s = score.conditions[cid]
        lines.append(
            f"{cid}: models={list(s.models) or ['?']} "
            f"attempts/task {s.min_attempts}-{s.max_attempts} "
            f"excluded={dict(s.excluded) or '{}'} "
            f"tool_disagreements={s.tool_disagreements}"
        )
    return "\n".join(lines)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rosetta.bench.report", description=__doc__)
    ap.add_argument(
        "--traces",
        default=str(DEFAULT_TRACES),
        help="a .jsonl file or a directory of them (default: results/bench/)",
    )
    ap.add_argument(
        "--tasks",
        default=str(build_mod.DEFAULT_OUT),
        help="task set built by rosetta.bench.build",
    )
    ap.add_argument("--no-tasks", action="store_true", help="skip the task-set cross-check")
    ap.add_argument("--summary", default=str(DEFAULT_SUMMARY))
    ap.add_argument("--detail", default=str(DEFAULT_DETAIL))
    ap.add_argument(
        "--print-only",
        action="store_true",
        help="render the table and write nothing",
    )
    args = ap.parse_args(argv)

    try:
        records = read_traces(args.traces)
    except FileNotFoundError as exc:
        print(
            f"ERROR: {exc}\nNo traces means no numbers. The website's pending "
            "state is the correct output until a real run exists.",
            file=sys.stderr,
        )
        return 2

    task_set = None
    if not args.no_tasks:
        try:
            task_set = build_mod.load_task_set(args.tasks)
        except FileNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    try:
        report = build_report(records, task_set=task_set)
    except ReportRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3

    print(render_text(report))
    if args.print_only:
        return 0
    written = write_report(
        report,
        summary_path=Path(args.summary),
        detail_path=Path(args.detail),
    )
    for path in written:
        print(f"wrote {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
