"""Tests for the benchmark: trace schema, admission gate, metrics, published report.

Every fixture here is synthetic and lives in this file. Nothing in ``results/``
is ever written by a test, and no fixture number is ever published.
"""

from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from rosetta.bench import build as B
from rosetta.bench import report as R
from rosetta.bench import run as RUN
from rosetta.bench import score as S
from rosetta.bench import split as SPLIT
from rosetta.bench import trace as T

REPO_ROOT = Path(__file__).resolve().parents[1]


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@dataclass
class FakeTask:
    """Stands in for ``rosetta.mutate.generate.MutationTask``."""

    task_id: str
    routine: str
    operator: str = "CMP_FLIP"
    difficulty: str = "easy"
    line_no: int = 3
    detail: str = "> -> <"
    mutated_src: str = "X ; mutated\n"
    baseline_src: str = "X ; original\n"
    cases: list[Any] = field(default_factory=lambda: [object()] * 5)
    n_diverged: int = 2
    divergence_kinds: tuple[str, ...] = ("global",)
    validated_by: str = "rosetta.core"
    timeout_only: bool = False


def rec(
    condition: str,
    task_id: str,
    *,
    attempt: int = 1,
    passed: bool = False,
    submitted: bool = True,
    claimed: bool | None = None,
    verifier_calls: int = 0,
    graded: bool = True,
    void: bool = False,
    harness_error: str | None = None,
    routine: str = "RTN",
    operator: str = "CMP_FLIP",
    tool_equivalent: bool | None = None,
) -> T.TraceRecord:
    calls = tuple(
        T.ToolCall(name="verify_change", equivalent=tool_equivalent)
        for _ in range(verifier_calls)
    )
    verdict = (
        T.Verdict(
            equivalent=passed,
            n_cases=5,
            n_diverged=0 if passed else 3,
            n_void=5 if void else 0,
        )
        if graded
        else None
    )
    return T.TraceRecord(
        condition=condition,
        task_id=task_id,
        attempt=attempt,
        routine=routine,
        operator=operator,
        difficulty="easy",
        model="fixture-model",
        submitted=submitted,
        candidate_src="X ; fixed\n" if submitted else None,
        assertion=T.Assertion(claimed_correct=claimed, source="explicit" if claimed is not None else "submission"),
        tool_calls=calls,
        verdict=verdict,
        harness_error=harness_error,
        run_id="fixture-run",
        extra={"protocol": T.PROTOCOL, "attempt_budget": 3, "model_timeout_s": 120.0,
               "agent_tools_enabled": False,
               "task_sha256": hashlib.sha256(task_id.encode()).hexdigest()},
    )


# ----------------------------------------------------------------------
# Trace schema
# ----------------------------------------------------------------------


class TestTraceSchema(unittest.TestCase):
    def test_round_trip_preserves_every_field(self) -> None:
        original = rec("scaffolded", "T1", verifier_calls=2, passed=True)
        again = T.TraceRecord.from_json(original.to_json())
        self.assertEqual(again.task_id, original.task_id)
        self.assertEqual(again.verifier_call_count, 2)
        self.assertTrue(again.passed)
        self.assertEqual(again.assertion.source, original.assertion.source)

    def test_unknown_fields_are_preserved_not_dropped(self) -> None:
        doc = rec("baseline", "T1").to_json()
        doc["cost_usd"] = 0.42
        doc["reasoning_tokens"] = 1234
        parsed = T.TraceRecord.from_json(doc)
        self.assertEqual(parsed.extra["cost_usd"], 0.42)
        self.assertEqual(parsed.to_json()["reasoning_tokens"], 1234)

    def test_missing_required_field_raises(self) -> None:
        doc = rec("baseline", "T1").to_json()
        del doc["task_id"]
        with self.assertRaises(T.TraceFormatError):
            T.TraceRecord.from_json(doc)

    def test_unknown_condition_raises(self) -> None:
        doc = rec("baseline", "T1").to_json()
        doc["condition"] = "vibes"
        with self.assertRaises(T.TraceFormatError):
            T.TraceRecord.from_json(doc)

    def test_bad_json_line_names_the_file_and_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "run.jsonl"
            p.write_text('{"condition":"baseline","task_id":"T1","attempt":1}\nnot json\n')
            with self.assertRaises(T.TraceFormatError) as ctx:
                T.read_traces(p)
            self.assertIn(":2", str(ctx.exception))

    def test_blank_lines_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = T.write_traces([rec("baseline", "T1")], Path(tmp) / "run.jsonl")
            p.write_text(p.read_text() + "\n\n")
            self.assertEqual(len(T.read_traces(p)), 1)

    def test_directory_of_jsonl_reads_every_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            T.write_traces([rec("baseline", "T1")], Path(tmp) / "a.jsonl")
            T.write_traces([rec("scaffolded", "T1")], Path(tmp) / "b.jsonl")
            self.assertEqual(len(T.read_traces(tmp)), 2)

    def test_demo_event_stream_gets_a_pointed_error(self) -> None:
        """results/ also holds the demo harness's events. Say so, do not guess."""
        with self.assertRaises(T.TraceFormatError) as ctx:
            T.TraceRecord.from_json({"run_id": "x", "seq": 0, "kind": "run_start"})
        self.assertIn("results/bench", str(ctx.exception))

    def test_void_verdict_is_not_scoreable(self) -> None:
        self.assertFalse(rec("baseline", "T1", void=True).scoreable)
        self.assertEqual(rec("baseline", "T1", void=True).exclusion_reason, "void")

    def test_ungraded_and_harness_error_are_distinct_exclusions(self) -> None:
        self.assertEqual(rec("baseline", "T1", graded=False).exclusion_reason, "ungraded")
        self.assertEqual(
            rec("baseline", "T1", harness_error="api 500").exclusion_reason,
            "harness_error",
        )


class TestAssertionRule(unittest.TestCase):
    """The judgement that drives the most important number in the project."""

    def test_submission_without_an_explicit_claim_counts_as_an_assertion(self) -> None:
        self.assertTrue(rec("baseline", "T1", submitted=True, claimed=None).asserted_correct)

    def test_no_submission_is_not_an_assertion(self) -> None:
        self.assertFalse(rec("baseline", "T1", submitted=False, claimed=None).asserted_correct)

    def test_explicit_hedge_overrides_the_submission_rule(self) -> None:
        self.assertFalse(rec("baseline", "T1", submitted=True, claimed=False).asserted_correct)

    def test_explicit_claim_without_submission_still_counts(self) -> None:
        self.assertTrue(rec("baseline", "T1", submitted=False, claimed=True).asserted_correct)


# ----------------------------------------------------------------------
# build.py -- admission
# ----------------------------------------------------------------------


class TestAdmission(unittest.TestCase):
    def setUp(self) -> None:
        self.eval_set = {"RTN", "OTHER"}

    def test_admits_a_valid_killable_mutant(self) -> None:
        verdict = B.admit(FakeTask("t1", "RTN"), self.eval_set)
        self.assertTrue(verdict.admitted)
        self.assertEqual(verdict.reason, "admitted")

    def test_rejects_a_train_split_routine(self) -> None:
        verdict = B.admit(FakeTask("t1", "TRAINONLY"), self.eval_set)
        self.assertFalse(verdict.admitted)
        self.assertEqual(verdict.reason, "not_in_eval_split")

    def test_rejects_a_stub_validated_task(self) -> None:
        task = FakeTask("t1", "RTN", validated_by="static-stub")
        self.assertFalse(B.admit(task, self.eval_set).admitted)
        self.assertTrue(B.admit(task, self.eval_set, allow_unvalidated=True).admitted)

    def test_timeout_only_is_weak_not_admitted(self) -> None:
        task = FakeTask("t1", "RTN", divergence_kinds=("timeout",))
        verdict = B.admit(task, self.eval_set)
        self.assertFalse(verdict.admitted)
        self.assertTrue(verdict.weak)
        self.assertEqual(verdict.reason, "timeout_only")

    def test_error_divergence_is_specific_enough(self) -> None:
        task = FakeTask("t1", "RTN", divergence_kinds=("error",))
        self.assertTrue(B.admit(task, self.eval_set).admitted)

    def test_rejects_too_few_cases(self) -> None:
        task = FakeTask("t1", "RTN", cases=[object()] * 4)
        self.assertEqual(B.admit(task, self.eval_set).reason, "too_few_cases")

    def test_rejects_a_mutant_no_case_separates(self) -> None:
        task = FakeTask("t1", "RTN", n_diverged=0)
        self.assertEqual(B.admit(task, self.eval_set).reason, "no_case_separates")

    def test_rejects_a_duplicate_mutant_source(self) -> None:
        seen: set[str] = set()
        first = FakeTask("t1", "RTN", operator="CMP_FLIP")
        second = FakeTask("t2", "RTN", operator="POSTCOND")
        self.assertTrue(B.admit(first, self.eval_set, seen_mutants=seen).admitted)
        verdict = B.admit(second, self.eval_set, seen_mutants=seen)
        self.assertFalse(verdict.admitted)
        self.assertEqual(verdict.reason, "duplicate_mutant")


class TestBuildTaskSet(unittest.TestCase):
    """The build loop, driven by an injected generator (no container needed)."""

    def _generated(self, tasks: list[FakeTask]) -> Any:
        @dataclass
        class Rejection:
            routine: str
            operator: str
            detail: str
            line_no: int
            reason: str

        @dataclass
        class Result:
            tasks: list[FakeTask]
            rejections: list[Rejection]

        return Result(tasks=tasks, rejections=[Rejection("RTN", "STMT_DROP", "-", 1, "equivalent")])

    def test_build_gates_and_records_provenance(self) -> None:
        lock = SPLIT.load_lock()
        names = [str(n) for n in lock["eval"][:2]]
        calls: list[str] = []

        def fake_generate(name, src, verifier, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(name)
            return self._generated(
                [
                    FakeTask(f"{name}-1", name, mutated_src=f"{name}-a"),
                    FakeTask(f"{name}-2", name, mutated_src=f"{name}-b", divergence_kinds=("timeout",)),
                ]
            )

        config = B.BuildConfig(routines=tuple(names), verifier="core")
        result = B.build_task_set(config, verifier=object(), generate=fake_generate)

        self.assertEqual(sorted(calls), sorted(names))
        self.assertEqual(len(result.tasks), 2)
        self.assertEqual(len(result.weak_tasks), 2)
        self.assertEqual(result.provenance["split_hash"], lock["content_hash"])
        self.assertTrue(result.provenance["publishable"])
        self.assertEqual(result.reason_counts()["equivalent"], 2)
        self.assertEqual(result.reason_counts()["timeout_only"], 2)
        self.assertEqual(set(result.routine_sources), set(names))

    def test_stub_validated_build_is_stamped_unpublishable(self) -> None:
        lock = SPLIT.load_lock()
        name = str(lock["eval"][0])

        def fake_generate(n, src, verifier, **kwargs):  # type: ignore[no-untyped-def]
            return self._generated([FakeTask(f"{n}-1", n, validated_by="static-stub")])

        config = B.BuildConfig(routines=(name,), verifier="static", allow_unvalidated=True)
        result = B.build_task_set(config, verifier=object(), generate=fake_generate)
        self.assertEqual(len(result.tasks), 1)
        self.assertFalse(result.provenance["publishable"])

    def test_asking_for_a_train_routine_is_refused(self) -> None:
        lock = SPLIT.load_lock()
        train_name = str(lock["train"][0])
        with self.assertRaises(B.TaskSetRefused):
            B.eval_routines(B.BuildConfig(routines=(train_name,)))

    def test_write_refuses_an_empty_task_set(self) -> None:
        with self.assertRaises(B.TaskSetRefused):
            B.write_task_set(B.TaskSet())

    def test_write_refuses_to_touch_the_split_lock(self) -> None:
        result = B.TaskSet(tasks=[FakeTask("t1", "RTN")])
        with self.assertRaises(B.TaskSetRefused):
            B.write_task_set(result, SPLIT.DEFAULT_LOCK)


# ----------------------------------------------------------------------
# score.py
# ----------------------------------------------------------------------


class TestScoring(unittest.TestCase):
    def test_pass_at_1_counts_only_attempt_one(self) -> None:
        records = [
            rec("baseline", "T1", attempt=1, passed=False),
            rec("baseline", "T1", attempt=2, passed=True),
            rec("baseline", "T2", attempt=1, passed=True),
        ]
        score = S.score_run(records)
        self.assertAlmostEqual(score.conditions["baseline"].pass_at_1, 0.5)

    def test_pass_at_3_needs_three_attempts_for_every_task(self) -> None:
        two_only = [rec("baseline", "T1", attempt=a) for a in (1, 2)]
        self.assertIsNone(S.score_run(two_only).conditions["baseline"].pass_at_3)
        three = [
            rec("baseline", "T1", attempt=a, passed=(a == 3)) for a in (1, 2, 3)
        ] + [rec("baseline", "T2", attempt=a) for a in (1, 2, 3)]
        score = S.score_run(three).conditions["baseline"]
        self.assertAlmostEqual(score.pass_at_1, 0.0)
        self.assertAlmostEqual(score.pass_at_3, 0.5)

    def test_false_confidence_is_over_all_scored_tasks(self) -> None:
        records = [
            rec("baseline", "T1", passed=False, submitted=True),   # confidently wrong
            rec("baseline", "T2", passed=True, submitted=True),    # right
            rec("baseline", "T3", passed=False, submitted=False),  # declined
            rec("baseline", "T4", passed=False, claimed=False),    # hedged
        ]
        score = S.score_run(records).conditions["baseline"]
        self.assertEqual(score.n_tasks, 4)
        self.assertEqual(score.n_false_confident, 1)
        self.assertAlmostEqual(score.false_confidence_rate, 0.25)
        self.assertAlmostEqual(score.assertion_rate, 0.5)
        self.assertAlmostEqual(score.false_confidence_given_assertion, 0.5)

    def test_false_confidence_never_exceeds_the_failure_mass(self) -> None:
        records = [rec("baseline", f"T{i}", passed=(i % 2 == 0)) for i in range(10)]
        score = S.score_run(records).conditions["baseline"]
        self.assertLessEqual(score.false_confidence_rate, 1 - score.pass_at_1 + 1e-9)

    def test_repair_iterations_count_verifier_calls_in_the_passing_attempt(self) -> None:
        records = [
            rec("scaffolded", "T1", attempt=1, passed=False, verifier_calls=1),
            rec("scaffolded", "T1", attempt=2, passed=True, verifier_calls=3),
            rec("scaffolded", "T2", attempt=1, passed=True, verifier_calls=1),
        ]
        score = S.score_run(records).conditions["scaffolded"]
        self.assertEqual(sorted(score.repair_iterations), [1, 3])
        self.assertAlmostEqual(score.mean_repair_iterations, 2.0)

    def test_tools_off_reports_no_repair_iterations_rather_than_zero(self) -> None:
        score = S.score_run([rec("baseline", "T1", passed=True)]).conditions["baseline"]
        self.assertFalse(score.tools_used)
        self.assertIsNone(score.mean_repair_iterations)

    def test_unscoreable_attempt_one_excludes_the_task_with_a_reason(self) -> None:
        records = [
            rec("baseline", "T1", void=True),
            rec("baseline", "T2", graded=False),
            rec("baseline", "T3", harness_error="timeout"),
            rec("baseline", "T4", passed=True),
        ]
        score = S.score_run(records).conditions["baseline"]
        self.assertEqual(score.n_tasks, 1)
        self.assertEqual(
            dict(score.excluded),
            {"void": 1, "ungraded": 1, "harness_error": 1},
        )

    def test_per_operator_breakdown(self) -> None:
        records = [
            rec("baseline", "T1", operator="CMP_FLIP", passed=True),
            rec("baseline", "T2", operator="CMP_FLIP", passed=False),
            rec("baseline", "T3", operator="NAKED_REF", passed=False),
        ]
        score = S.score_run(records).conditions["baseline"]
        self.assertAlmostEqual(score.per_operator["CMP_FLIP"].pass_at_1, 0.5)
        self.assertAlmostEqual(score.per_operator["NAKED_REF"].pass_at_1, 0.0)
        self.assertAlmostEqual(score.per_operator["NAKED_REF"].false_confidence_rate, 1.0)

    def test_tool_disagreement_is_counted(self) -> None:
        records = [
            rec("scaffolded", "T1", passed=False, verifier_calls=1, tool_equivalent=True)
        ]
        score = S.score_run(records).conditions["scaffolded"]
        self.assertEqual(score.tool_disagreements, 1)

    def test_common_tasks_are_the_intersection(self) -> None:
        records = [
            rec("baseline", "T1", passed=True),
            rec("baseline", "T2", passed=True),
            rec("scaffolded", "T1", passed=True),
            rec("scaffolded", "T3", passed=True),
        ]
        score = S.score_run(records, common_only=True)
        self.assertEqual(score.common_task_ids, ("T1",))
        self.assertEqual(score.conditions["baseline"].n_tasks, 1)

    def test_duplicate_attempts_are_reported(self) -> None:
        records = [rec("baseline", "T1"), rec("baseline", "T1")]
        self.assertEqual(
            S.score_run(records).duplicate_attempts, ("baseline/T1#1",)
        )

    def test_empty_traces_raise(self) -> None:
        with self.assertRaises(S.ScoringError):
            S.score_run([])


# ----------------------------------------------------------------------
# report.py
# ----------------------------------------------------------------------


def _eval_names(n: int = 2) -> list[str]:
    return [str(x) for x in SPLIT.load_lock()["eval"][:n]]


def _traces_and_task_set(n_tasks: int = 4) -> tuple[list[T.TraceRecord], dict[str, Any]]:
    routine = _eval_names(1)[0]
    task_ids = [f"{routine}-CMP_FLIP-L{i}-abcd{i:04d}" for i in range(n_tasks)]
    records: list[T.TraceRecord] = []
    for i, tid in enumerate(task_ids):
        records.append(
            rec("baseline", tid, passed=(i == 0), routine=routine, submitted=True)
        )
        records.append(
            rec(
                "scaffolded",
                tid,
                passed=(i < 3),
                routine=routine,
                submitted=True,
                verifier_calls=2,
            )
        )
    task_set = {
        "schema": B.SCHEMA,
        "provenance": {
            "split_hash": SPLIT.load_lock()["content_hash"],
            "publishable": True,
            "verifier": "core",
        },
        "counts": {"tasks": n_tasks},
        "tasks": [{"task_id": t, "routine": routine} for t in task_ids],
    }
    return records, task_set


class TestReport(unittest.TestCase):
    def test_refuses_legacy_and_mixed_protocols(self) -> None:
        records, task_set = _traces_and_task_set()
        records[0].extra.pop("protocol")
        with self.assertRaisesRegex(R.ReportRefused, "protocol"):
            R.build_report(records, task_set=task_set)

    def test_refuses_mixed_runs_models_and_budgets(self) -> None:
        from dataclasses import replace
        for changes in ({"run_id": "different"}, {"model": "another-model"},
                        {"extra": {"attempt_budget": 2}}):
            records, task_set = _traces_and_task_set()
            if "extra" in changes:
                changes = {"extra": {**records[0].extra, **changes["extra"]}}
            records[0] = replace(records[0], **changes)
            with self.assertRaises(R.ReportRefused):
                R.build_report(records, task_set=task_set)

    def test_refuses_reference_or_suite_changed_after_run(self) -> None:
        records, task_set = _traces_and_task_set()
        for task in task_set["tasks"]:
            task.update(baseline_src="RTN ; reference", mutated_src="RTN ; mutant", cases=[])
            for record in records:
                if record.task_id == task["task_id"]:
                    record.extra["task_sha256"] = T.task_fingerprint(task)
        R.build_report(records, task_set=task_set)
        task_set["tasks"][0]["baseline_src"] += "corruption"
        with self.assertRaisesRegex(R.ReportRefused, "fingerprint"):
            R.build_report(records, task_set=task_set)

    def test_detail_identifies_protocol_and_partial_coverage(self) -> None:
        records, task_set = _traces_and_task_set()
        report = R.build_report(records[:2], task_set=task_set)
        self.assertTrue(report.detail["coverage"]["partial"])
        self.assertEqual(report.detail["coverage"]["common_scoreable_task_count"], 1)
        self.assertEqual(report.detail["protocol"]["id"], T.PROTOCOL)

    def test_bounded_repair_counts_early_success_and_refuses_incomplete_rate(self) -> None:
        records, task_set = _traces_and_task_set(1)
        # A pass on attempt one completes a budgeted task without extra sampling.
        report = R.build_report(records, task_set=task_set)
        self.assertEqual(report.detail["bounded_attempts"]["baseline"]["success_rate"], 1)
        records = [rec(c, records[0].task_id, routine=records[0].routine)
                   for c in ("baseline", "scaffolded")]
        report = R.build_report(records, task_set=task_set)
        self.assertIsNone(report.detail["bounded_attempts"]["baseline"]["success_rate"])
        for c in ("baseline", "scaffolded"):
            records.extend(rec(c, records[0].task_id, routine=records[0].routine,
                               attempt=a, passed=(a == 2)) for a in (2, 3))
        report = R.build_report(records, task_set=task_set)
        self.assertEqual(report.detail["bounded_attempts"]["scaffolded"]["success_rate"], 1)
        self.assertEqual(report.detail["bounded_attempts"]["scaffolded"]["mean_attempts_to_success"], 2)

    def test_partial_comparison_preserves_provider_exclusions(self) -> None:
        from dataclasses import replace
        records, task_set = _traces_and_task_set(2)
        records[1] = replace(records[1], verdict=None, harness_error="provider timeout")
        report = R.build_report(records, task_set=task_set)
        excluded = report.detail["coverage"]["condition_exclusions"]["scaffolded"]
        self.assertEqual(excluded["reasons"], {"harness_error": 1})
        self.assertEqual(excluded["task_exclusions"], [records[1].task_id + ":harness_error"])
        with self.assertRaisesRegex(R.ReportRefused, "harness_error"):
            R.build_report(records[:2], task_set=task_set)

    def test_summary_matches_the_published_schema(self) -> None:
        records, task_set = _traces_and_task_set()
        report = R.build_report(records, task_set=task_set)
        summary = report.summary
        self.assertEqual(summary["schema_version"], 1)
        self.assertEqual(summary["task_count"], 4)
        self.assertEqual(summary["split_hash"], SPLIT.load_lock()["content_hash"])
        self.assertEqual([c["id"] for c in summary["conditions"]], ["baseline", "scaffolded"])
        self.assertAlmostEqual(summary["conditions"][0]["pass_at_1"], 0.25)
        self.assertAlmostEqual(summary["conditions"][1]["pass_at_1"], 0.75)
        self.assertEqual(set(summary), {
            "schema_version", "generated_at", "task_count", "split_hash", "conditions",
        })
        for cond in summary["conditions"]:
            self.assertEqual(set(cond), {"id", "pass_at_1", "false_confidence_rate"})

    def test_detail_carries_the_richer_view(self) -> None:
        records, task_set = _traces_and_task_set()
        detail = R.build_report(records, task_set=task_set).detail
        scaffolded = detail["run"]["conditions"]["scaffolded"]
        self.assertIn("per_operator", scaffolded)
        self.assertEqual(scaffolded["mean_repair_iterations"], 2.0)
        self.assertIn("false_confidence_rate", detail["scoring"])

    def test_task_facts_are_filled_in_from_the_task_set(self) -> None:
        """A producer that omits operator/difficulty still gets a breakdown."""
        records, task_set = _traces_and_task_set()
        for t in task_set["tasks"]:
            t["operator"] = "NAKED_REF"
            t["difficulty"] = "hard"
        bare = [
            T.TraceRecord.from_json({**r.to_json(), "operator": "", "difficulty": ""})
            for r in records
        ]
        detail = R.build_report(bare, task_set=task_set).detail
        self.assertIn("NAKED_REF", detail["run"]["conditions"]["baseline"]["per_operator"])

    def test_refuses_without_both_required_conditions(self) -> None:
        records, task_set = _traces_and_task_set()
        only_baseline = [r for r in records if r.condition == "baseline"]
        with self.assertRaises(R.ReportRefused):
            R.build_report(only_baseline, task_set=task_set)

    def test_refuses_a_task_id_outside_the_built_task_set(self) -> None:
        records, task_set = _traces_and_task_set()
        task_set["tasks"] = task_set["tasks"][:1]
        with self.assertRaises(R.ReportRefused):
            R.build_report(records, task_set=task_set)

    def test_refuses_an_unpublishable_task_set(self) -> None:
        records, task_set = _traces_and_task_set()
        task_set["provenance"]["publishable"] = False
        with self.assertRaises(R.ReportRefused):
            R.build_report(records, task_set=task_set)

    def test_refuses_a_task_set_built_against_another_split(self) -> None:
        records, task_set = _traces_and_task_set()
        task_set["provenance"]["split_hash"] = "0" * 64
        with self.assertRaises(R.ReportRefused):
            R.build_report(records, task_set=task_set)

    def test_refuses_traces_naming_a_train_split_routine(self) -> None:
        records, task_set = _traces_and_task_set()
        train = str(SPLIT.load_lock()["train"][0])
        leaked = [
            T.TraceRecord.from_json({**r.to_json(), "routine": train}) for r in records
        ]
        with self.assertRaises(R.ReportRefused) as ctx:
            R.build_report(leaked, task_set=task_set)
        self.assertIn("train-split", str(ctx.exception))

    def test_refuses_when_no_task_is_common_to_both_conditions(self) -> None:
        routine = _eval_names(1)[0]
        records = [
            rec("baseline", "A", routine=routine, passed=True),
            rec("scaffolded", "B", routine=routine, passed=True),
        ]
        with self.assertRaises(R.ReportRefused):
            R.build_report(records)

    def test_refuses_empty_traces(self) -> None:
        with self.assertRaises(R.ReportRefused):
            R.build_report([])

    def test_validate_summary_rejects_what_the_site_rejects(self) -> None:
        good = {
            "schema_version": 1,
            "generated_at": "2026-09-05T12:00:00Z",
            "task_count": 20,
            "split_hash": "a" * 64,
            "conditions": [
                {"id": "baseline", "pass_at_1": 0, "false_confidence_rate": 1},
                {"id": "scaffolded", "pass_at_1": 1, "false_confidence_rate": 0},
            ],
        }
        R.validate_summary(good)
        for key, value in [
            ("schema_version", 2),
            ("task_count", 0),
            ("split_hash", "missing"),
            ("generated_at", "bad date"),
        ]:
            with self.assertRaises(R.ReportRefused):
                R.validate_summary({**good, key: value})
        for bad_rate in [float("nan"), float("inf"), -0.1, 1.1, 31, "0.3", None]:
            candidate = json.loads(json.dumps(good, default=str))
            candidate["conditions"][0]["pass_at_1"] = bad_rate
            with self.assertRaises(R.ReportRefused):
                R.validate_summary(candidate)
        duplicated = json.loads(json.dumps(good))
        duplicated["conditions"][1]["id"] = "baseline"
        with self.assertRaises(R.ReportRefused):
            R.validate_summary(duplicated)

    def test_writes_only_where_told(self) -> None:
        records, task_set = _traces_and_task_set()
        report = R.build_report(records, task_set=task_set)
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.json"
            detail = Path(tmp) / "report.json"
            R.write_report(report, summary_path=summary, detail_path=detail)
            self.assertEqual(json.loads(summary.read_text()), report.summary)
            self.assertTrue(detail.exists())
        self.assertFalse(
            (REPO_ROOT / "results" / "summary.json").exists()
            and "fixture-model" in (REPO_ROOT / "results" / "summary.json").read_text(),
            "a test must never publish into results/",
        )

    def test_text_view_renders(self) -> None:
        records, task_set = _traces_and_task_set()
        text = R.render_text(R.build_report(records, task_set=task_set))
        self.assertIn("pass@1", text)
        self.assertIn("per operator", text)
        self.assertIn("CMP_FLIP", text)
        self.assertIn("repair≤3", text)
        self.assertIn("Harness feedback", text)
        self.assertNotIn("pass@3", text)
        self.assertNotIn("pass_at_3", R.build_report(records, task_set=task_set).detail["run"]["conditions"]["scaffolded"])


class TestBenchmarkExecution(unittest.TestCase):
    def task(self):
        return {"task_id": "fixture", "routine": "RTN", "baseline_src": "RTN ; original\n",
                "mutated_src": "RTN ; mutant\n", "cases": [{"routine": "RTN", "entry": ""}]}

    def test_corrupted_reference_is_rejected_before_model_calls(self):
        task = self.task()
        manifest = {"RTN": hashlib.sha256(task["baseline_src"].encode()).hexdigest()}
        task["baseline_src"] += "corrupted"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            path.write_text(json.dumps({"tasks": [task], "routine_sources": manifest}))
            with self.assertRaisesRegex(RUN.RunRefused, "source manifest"):
                RUN.load_taskset(path)

    def test_attempts_persist_before_later_interruption(self):
        agent = Mock(transcripts=[])
        agent.propose.side_effect = [Mock(candidate_src="RTN ; candidate\n", explanation=""),
                                     KeyboardInterrupt()]
        from rosetta.core.interface import VerifyReport
        verify = Mock(return_value=VerifyReport(equivalent=False, n_cases=1, n_diverged=1,
                                               divergences=[]))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            with self.assertRaises(KeyboardInterrupt):
                RUN.run_task(self.task(), "scaffolded", agent, verify, "test", "model", 2,
                             on_record=lambda r: T.write_traces([r], path, append=True))
            records = T.read_traces(path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].extra["protocol"], T.PROTOCOL)
            self.assertEqual(records[0].extra["feedback_received"], [])

    def test_decline_is_preserved_as_ungraded_attempt(self):
        agent = Mock(transcripts=[])
        agent.propose.return_value = None
        records = RUN.run_task(self.task(), "baseline", agent, Mock(), "test", "model", 2)
        self.assertEqual(len(records), 1)
        self.assertIn("no candidate", records[0].harness_error)

    def test_model_timeout_is_forwarded_and_errors_persist(self):
        with tempfile.TemporaryDirectory() as tmp, patch("rosetta.demo.agents.OpenCodeAgent") as cls:
            cls.return_value.propose.side_effect = TimeoutError("fixture timeout")
            path = RUN.run_benchmark({"tasks": [self.task()]}, ["baseline", "scaffolded"],
                                     "opencode", "fixture-model", 1, Path(tmp), model_timeout_s=7)
            self.assertEqual(cls.call_args.kwargs["timeout_s"], 7)
            records = T.read_traces(path)
            self.assertEqual(len(records), 2)
            self.assertTrue(all("timeout" in r.harness_error for r in records))


if __name__ == "__main__":
    unittest.main()
