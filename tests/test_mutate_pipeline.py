"""Tests for input-suite generation, the verifier seam and task admission."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from rosetta.core.interface import Divergence, ExecSpec, VerifyReport
from rosetta.mutate.cases import (
    MIN_CASES,
    JsonGlobalSampler,
    NullSampler,
    build_cases,
    expand_pattern,
    free_locals,
    harvest_literals,
    profile_entry,
)
from rosetta.mutate.generate import (
    MutationTask,
    generate_for_routine,
    generate_tasks,
    load_split,
    write_tasks,
)
from rosetta.mutate.lex import parse_routine
from rosetta.mutate.verify import (
    CoreNotAvailable,
    CoreVerifier,
    RecordingVerifier,
    StaticVerifier,
    Verifier,
    resolve_verifier,
)

ROUTINE_DIR = Path(__file__).resolve().parent.parent / "data" / "routines"

TINY = "T ;header\nGO(A,B) ;\n N C\n S C=A+B\n Q C\n"


# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------


@dataclass
class FixedVerifier:
    """Returns a canned report for every call, so admission logic is testable."""

    report: VerifyReport
    name: str = "fixed"
    seen: list[str] = field(default_factory=list)

    def verify_equivalence(
        self, routine: str, baseline_src: str, candidate_src: str, cases: list[ExecSpec]
    ) -> VerifyReport:
        self.seen.append(candidate_src)
        return self.report


@dataclass
class RaisingVerifier:
    """Simulates ``load_routine`` rejecting a mutant that will not compile."""

    name: str = "raising"

    def verify_equivalence(self, *_a, **_k) -> VerifyReport:
        raise RuntimeError("M(rror): label not found")


def _report(*divs: Divergence, equivalent: bool = False, n: int = 8) -> VerifyReport:
    return VerifyReport(
        equivalent=equivalent,
        divergences=list(divs),
        n_cases=n,
        n_diverged=0 if equivalent else max(1, len(divs)),
    )


# --------------------------------------------------------------------------
# Pattern synthesis
# --------------------------------------------------------------------------


class PatternExpansionTests(unittest.TestCase):
    def test_fixed_repetition_of_a_code_class(self) -> None:
        got = expand_pattern("12UN")
        self.assertIsNotNone(got)
        self.assertEqual(len(got), 12)  # type: ignore[arg-type]
        self.assertTrue(got.isupper())  # type: ignore[union-attr]

    def test_literal_inside_a_pattern_is_reproduced(self) -> None:
        self.assertEqual(expand_pattern('3N1"-"4N'), "111-1111")

    def test_variants_differ(self) -> None:
        self.assertNotEqual(expand_pattern("12UN", 0), expand_pattern("12UN", 1))

    def test_alternation_is_refused_rather_than_guessed(self) -> None:
        self.assertIsNone(expand_pattern("1(1N,1U)"))

    def test_empty_pattern_is_refused(self) -> None:
        self.assertIsNone(expand_pattern(""))


# --------------------------------------------------------------------------
# Input suites
# --------------------------------------------------------------------------


class CaseBuildingTests(unittest.TestCase):
    def test_meets_the_section_9_minimum(self) -> None:
        cases = build_cases(parse_routine("T", TINY), "GO")
        self.assertGreaterEqual(len(cases), MIN_CASES)

    def test_every_case_names_the_routine_and_entry(self) -> None:
        for c in build_cases(parse_routine("T", TINY), "GO"):
            self.assertEqual(c.routine, "T")
            self.assertEqual(c.entry, "GO")

    def test_includes_the_boundary_values(self) -> None:
        args = [c.args for c in build_cases(parse_routine("T", TINY), "GO")]
        self.assertIn(["", ""], args)

    def test_includes_a_short_argument_list_for_the_undefined_local_case(self) -> None:
        args = [c.args for c in build_cases(parse_routine("T", TINY), "GO")]
        self.assertTrue(any(len(a) < 2 for a in args))

    def test_unknown_label_fails_loudly(self) -> None:
        with self.assertRaises(ValueError):
            build_cases(parse_routine("T", TINY), "NOSUCH")

    def test_argumentless_entry_still_gets_a_suite(self) -> None:
        src = "T ;h\nGO ;\n S X=1\n Q\n"
        self.assertTrue(build_cases(parse_routine("T", src), "GO"))

    def test_free_locals_are_seeded(self) -> None:
        # DVARS reads %VLIST, which it never assigns; without seeding it, every
        # case would be an undefined-local error and nothing would discriminate.
        src = (
            "T ;h\n"
            "SPLIT(%SRC,%DLM,%VLIST) ;\n Q 1\n"
            'DVARS(LIST) ;\n Q $S(%VLIST[";":";",1:",")\n'
        )
        r = parse_routine("T", src)
        self.assertIn("%VLIST", free_locals(r, "DVARS"))
        self.assertIn("%VLIST", build_cases(r, "DVARS")[0].locals_in)


class ProfilingTests(unittest.TestCase):
    def test_pattern_constraint_is_captured(self) -> None:
        src = "T ;h\nV(S) ;\n I S'?12UN Q 0\n Q 1\n"
        p = profile_entry(parse_routine("T", src), "V")[0]
        self.assertEqual(p.patterns, ["12UN"])

    def test_tested_literal_is_captured_from_the_raw_line(self) -> None:
        # The literal survives only in the raw line; the masked line has it
        # replaced by placeholders.
        src = 'T ;h\nV(S) ;\n I S["O" Q 0\n Q 1\n'
        p = profile_entry(parse_routine("T", src), "V")[0]
        self.assertEqual(p.literals, ["O"])

    def test_for_bound_and_index_positions_imply_numeric(self) -> None:
        src = "T ;h\nV(W) ;\n F I=1:1:W S X=1\n Q X\n"
        self.assertIn("numeric", profile_entry(parse_routine("T", src), "V")[0].kinds)

    def test_harvested_literals_come_from_code_not_comments(self) -> None:
        src = 'T ;h\nGO ;\n S X="ABC" ;"NOTTHIS"\n Q\n'
        self.assertEqual(harvest_literals(parse_routine("T", src)), ["ABC"])


class SamplerTests(unittest.TestCase):
    def test_null_sampler_invents_nothing(self) -> None:
        self.assertEqual(NullSampler().sample("^DPT", 5), [])

    def test_json_sampler_returns_recorded_pairs(self) -> None:
        s = JsonGlobalSampler({"^DPT": [["^DPT(3,0)", "SMITH,JOHN"]]})
        self.assertEqual(s.sample("^DPT", 5), [("^DPT(3,0)", "SMITH,JOHN")])

    def test_sampled_globals_reach_the_exec_spec(self) -> None:
        src = "T ;h\nGO(A) ;\n Q $G(^DPT(A,0))\n"
        cases = build_cases(
            parse_routine("T", src),
            "GO",
            sampler=JsonGlobalSampler({"^DPT": [["^DPT(3,0)", "SMITH,JOHN"]]}),
        )
        self.assertEqual(cases[0].globals_in, {"^DPT(3,0)": "SMITH,JOHN"})


# --------------------------------------------------------------------------
# Verifier seam
# --------------------------------------------------------------------------


class VerifierSeamTests(unittest.TestCase):
    def test_static_verifier_satisfies_the_protocol(self) -> None:
        self.assertIsInstance(StaticVerifier(), Verifier)

    def test_static_verifier_calls_identical_source_equivalent(self) -> None:
        r = StaticVerifier().verify_equivalence("T", "a", "a", [])
        self.assertTrue(r.equivalent)

    def test_static_verifier_marks_its_divergence_as_unexecuted(self) -> None:
        # Anyone inspecting the report must be able to see no execution happened.
        r = StaticVerifier().verify_equivalence("T", "a", "b", [])
        self.assertFalse(r.equivalent)
        self.assertEqual(r.divergences[0].ref, "static-stub")

    def test_recording_verifier_delegates_and_records(self) -> None:
        rec = RecordingVerifier(StaticVerifier())
        rec.verify_equivalence("T", "a", "b", [ExecSpec(routine="T")])
        self.assertEqual(rec.calls, [("T", 1)])

    def test_resolve_verifier_rejects_an_unknown_kind(self) -> None:
        with self.assertRaises(ValueError):
            resolve_verifier("magic")

    def test_core_verifier_reports_a_missing_core_loudly(self) -> None:
        v = CoreVerifier()
        try:
            v._resolve()
        except CoreNotAvailable as exc:
            self.assertIn("verify_equivalence", str(exc))


# --------------------------------------------------------------------------
# Admission
# --------------------------------------------------------------------------


class AdmissionTests(unittest.TestCase):
    def test_equivalent_mutants_are_discarded(self) -> None:
        got = generate_for_routine(
            "T", TINY, FixedVerifier(_report(equivalent=True)), max_tasks=5
        )
        self.assertEqual(got.tasks, [])
        self.assertIn("equivalent", got.reason_counts())

    def test_a_named_divergence_is_admitted(self) -> None:
        div = Divergence(kind="global", ref="^DPT(3,0)", expected="a", actual="b")
        got = generate_for_routine("T", TINY, FixedVerifier(_report(div)), max_tasks=3)
        self.assertTrue(got.tasks)
        self.assertEqual(got.tasks[0].divergence_kinds, ("global",))
        self.assertFalse(got.tasks[0].timeout_only)

    def test_a_bare_timeout_is_rejected_as_a_weak_signal(self) -> None:
        div = Divergence(kind="timeout", ref="", expected="", actual="")
        got = generate_for_routine("T", TINY, FixedVerifier(_report(div)), max_tasks=3)
        self.assertEqual(got.tasks, [])
        self.assertIn("timeout_only", got.reason_counts())

    def test_a_bare_timeout_can_be_admitted_explicitly_and_is_flagged(self) -> None:
        div = Divergence(kind="timeout", ref="", expected="", actual="")
        got = generate_for_routine(
            "T", TINY, FixedVerifier(_report(div)), max_tasks=1, allow_timeout_only=True
        )
        self.assertTrue(got.tasks[0].timeout_only)

    def test_an_error_code_counts_as_a_specific_divergence(self) -> None:
        div = Divergence(kind="error", ref="error", expected="", actual="M6")
        got = generate_for_routine("T", TINY, FixedVerifier(_report(div)), max_tasks=1)
        self.assertTrue(got.tasks)

    def test_a_mutant_that_fails_to_load_is_rejected_not_admitted(self) -> None:
        got = generate_for_routine("T", TINY, RaisingVerifier(), max_tasks=3)
        self.assertEqual(got.tasks, [])
        self.assertTrue(
            all(r.reason.startswith("load_failed") for r in got.rejections)
        )

    def test_a_baseline_with_a_tp_command_is_refused(self) -> None:
        from rosetta.mutate.lex import TPCommandError

        with self.assertRaises(TPCommandError):
            generate_for_routine("T", "T ;h\n TSTART\n S X=1\n", StaticVerifier())

    def test_task_records_which_verifier_admitted_it(self) -> None:
        got = generate_for_routine("T", TINY, StaticVerifier(), max_tasks=1)
        self.assertEqual(got.tasks[0].validated_by, "static-stub")

    def test_task_ids_are_unique_and_deterministic(self) -> None:
        a = generate_for_routine("T", TINY, StaticVerifier())
        b = generate_for_routine("T", TINY, StaticVerifier())
        ids = [t.task_id for t in a.tasks]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, [t.task_id for t in b.tasks])

    def test_every_task_carries_at_least_the_minimum_cases(self) -> None:
        for t in generate_for_routine("T", TINY, StaticVerifier()).tasks:
            self.assertGreaterEqual(len(t.cases), MIN_CASES)

    def test_task_field_order_matches_the_specification(self) -> None:
        # PROJECT.md section 6 fixes the first eight fields and their order.
        spec = ExecSpec(routine="T")
        t = MutationTask("id", "T", "a", "b", "CMP_FLIP", 3, [spec], "easy")
        self.assertEqual(t.operator, "CMP_FLIP")
        self.assertEqual(t.line_no, 3)
        self.assertEqual(t.difficulty, "easy")


class SplitTests(unittest.TestCase):
    def test_missing_split_is_fatal_by_default(self) -> None:
        with self.assertRaises(FileNotFoundError):
            generate_tasks(
                [("T", TINY)], StaticVerifier(), split_path="/nonexistent/split.json"
            )

    def test_missing_split_can_be_waived_explicitly(self) -> None:
        got = generate_tasks(
            [("T", TINY)],
            StaticVerifier(),
            split_path="/nonexistent/split.json",
            require_split=False,
            max_per_routine=1,
        )
        self.assertTrue(got.tasks)

    def test_routines_outside_the_eval_split_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "split.lock.json"
            p.write_text(json.dumps({"train": ["T"], "eval": ["OTHER"]}))
            got = generate_tasks([("T", TINY)], StaticVerifier(), split_path=p)
            self.assertEqual(got.tasks, [])
            self.assertEqual(got.reason_counts(), {"not_in_eval_split": 1})

    def test_routines_inside_the_eval_split_are_generated(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "split.lock.json"
            p.write_text(json.dumps({"train": ["OTHER"], "eval": ["T"]}))
            got = generate_tasks(
                [("T", TINY)], StaticVerifier(), split_path=p, max_per_routine=2
            )
            self.assertTrue(got.tasks)

    def test_load_split_returns_none_when_absent(self) -> None:
        self.assertIsNone(load_split("/nonexistent/split.lock.json"))


class SerialisationTests(unittest.TestCase):
    def test_written_document_round_trips_and_carries_the_tally(self) -> None:
        got = generate_for_routine("T", TINY, StaticVerifier(), max_tasks=3)
        with tempfile.TemporaryDirectory() as d:
            path = write_tasks(got, Path(d) / "tasks.json")
            doc = json.loads(path.read_text())
        self.assertEqual(doc["counts"]["tasks"], len(got.tasks))
        self.assertIn("by_operator", doc["counts"])
        self.assertEqual(doc["validated_by"], ["static-stub"])
        self.assertEqual(
            doc["tasks"][0]["cases"][0]["routine"], got.tasks[0].cases[0].routine
        )


@unittest.skipUnless(ROUTINE_DIR.is_dir(), "data/routines not extracted")
class ShortlistPipelineTests(unittest.TestCase):
    def test_prchuei_produces_tasks_across_several_operators(self) -> None:
        src = (ROUTINE_DIR / "PRCHUEI.m").read_text(errors="replace")
        got = generate_for_routine("PRCHUEI", src, StaticVerifier())
        self.assertGreater(len(got.tasks), 20)
        self.assertGreaterEqual(len(got.by_operator()), 5)

    def test_cases_reach_past_the_input_validator(self) -> None:
        # `$$VALIDUEI` rejects anything that is not 12 uppercase alphanumerics
        # on its first line. At least one case must satisfy the pattern or no
        # mutation inside the checksum loop could ever be killed.
        src = (ROUTINE_DIR / "PRCHUEI.m").read_text(errors="replace")
        cases = build_cases(parse_routine("PRCHUEI", src), "VALIDUEI")
        conforming = [
            c for c in cases if c.args and len(c.args[0]) == 12 and c.args[0].isalnum()
        ]
        self.assertTrue(conforming)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
