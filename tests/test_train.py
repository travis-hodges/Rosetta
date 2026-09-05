"""Tests for comprehension labels, the SFT builder and the RFT grader."""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from rosetta.bench.split import load_lock
from rosetta.core.interface import Divergence, ExecSpec, VerifyReport
from rosetta.train import labels, sft
from rosetta.train.grader import RewardGrader


class TestNakedReferenceResolution(unittest.TestCase):
    """A wrong deterministic label is worse than no label: it teaches the
    model something false while looking authoritative."""

    def test_naked_ref_replaces_only_the_last_subscript(self) -> None:
        self.assertEqual(labels.resolve_naked("^DPT(3,0)", "1"), "^DPT(3,1)")

    def test_multi_subscript_naked_ref(self) -> None:
        self.assertEqual(labels.resolve_naked("^X(1,2,3)", "9,8"), "^X(1,2,9,8)")

    def test_same_line_reference_wins(self) -> None:
        """The bug this test exists for: the preceding reference can be
        earlier on the SAME line, not only on a previous one."""
        src = (
            "T ;\n"
            "GO ;\n"
            ' S:$D(^DENT(226,DA,.1)) X=$P(^(.1),"^",1)\n'
        )
        facts = {"name": "T"}
        pairs = list(labels.idiom_pairs(facts, src))
        self.assertTrue(pairs, "no naked reference detected")
        self.assertEqual(pairs[0].answer, "^DENT(226,DA,.1)")
        self.assertIn("^DENT(226,DA,.1)", pairs[0].question)

    def test_unmasked_source_is_quoted(self) -> None:
        """String subscripts must survive into the label, not appear as ""."""
        src = 'T ;\nGO ;\n S X=^DENT(226,"A",1),Y=^(2)\n'
        pairs = list(labels.idiom_pairs({"name": "T"}, src))
        self.assertTrue(pairs)
        self.assertIn('"A"', pairs[0].question)
        self.assertEqual(pairs[0].answer, '^DENT(226,"A",2)')

    def test_refuses_when_previous_has_no_subscripts(self) -> None:
        self.assertIsNone(labels.resolve_naked("^XTMP", "1"))

    def test_split_subscripts_respects_nesting(self) -> None:
        name, subs = labels.split_subscripts("^X($P(A,U,1),2)")
        self.assertEqual(name, "^X")
        self.assertEqual(subs, ["$P(A,U,1)", "2"])


class TestLabelGeneration(unittest.TestCase):
    def test_generates_from_train_split_only(self) -> None:
        pairs = labels.generate(limit=40)
        held_out = set(load_lock()["eval"])
        self.assertFalse({p.routine for p in pairs} & held_out)

    def test_all_five_sources_fire(self) -> None:
        pairs = labels.generate(limit=60)
        self.assertEqual(
            {p.source for p in pairs},
            {"call_graph", "fileman", "globals", "idiom", "structure"},
        )

    def test_answers_are_never_empty(self) -> None:
        for p in labels.generate(limit=40):
            self.assertTrue(p.answer.strip(), f"empty answer: {p.question[:60]}")


class TestSFT(unittest.TestCase):
    def test_leakage_check_catches_an_eval_routine(self) -> None:
        evil = labels.QAPair("q", "a", "structure", load_lock()["eval"][0])
        with self.assertRaises(RuntimeError):
            sft.assert_no_eval_leakage([evil])

    def test_examples_are_chat_shaped(self) -> None:
        ex = sft.to_example(labels.QAPair("Q?", "A.", "structure", "R"))
        self.assertEqual([m["role"] for m in ex.messages],
                         ["system", "user", "assistant"])
        self.assertEqual(ex.messages[-1]["content"], "A.")


@dataclass
class FakeVerifier:
    report: VerifyReport | None = None
    raises: Exception | None = None

    def verify_equivalence(self, routine, baseline_src, candidate_src, cases):
        if self.raises:
            raise self.raises
        assert self.report is not None
        return self.report


def _report(equivalent, n_cases, n_diverged, n_void=0, divs=()):
    return VerifyReport(
        equivalent=equivalent,
        divergences=list(divs),
        n_cases=n_cases,
        n_diverged=n_diverged,
        n_void=n_void,
    )


class TestGrader(unittest.TestCase):
    def _grade(self, report, shaping="binary"):
        g = RewardGrader(FakeVerifier(report=report), shaping=shaping)
        return g.grade("R", "base", "cand", [ExecSpec(routine="R")])

    def test_equivalent_scores_one(self) -> None:
        self.assertEqual(self._grade(_report(True, 5, 0)).reward, 1.0)

    def test_divergent_scores_zero_when_binary(self) -> None:
        r = self._grade(_report(False, 5, 2))
        self.assertEqual(r.reward, 0.0)
        self.assertFalse(r.passed)

    def test_shaped_gives_partial_credit(self) -> None:
        r = self._grade(_report(False, 10, 1), shaping="shaped")
        self.assertAlmostEqual(r.reward, 0.9)
        self.assertFalse(r.passed, "partial credit must not count as a pass")

    def test_void_cases_are_excluded_from_the_denominator(self) -> None:
        """Void cases carry no information; scoring them trains on noise."""
        r = self._grade(_report(False, 10, 1, n_void=5), shaping="shaped")
        self.assertAlmostEqual(r.reward, 4 / 5)

    def test_all_void_is_ungradable(self) -> None:
        r = self._grade(_report(False, 3, 0, n_void=3))
        self.assertEqual(r.reward, 0.0)
        self.assertIn("void", r.reason)

    def test_verifier_exception_returns_a_reward_not_a_crash(self) -> None:
        g = RewardGrader(FakeVerifier(raises=RuntimeError("boom")))
        r = g.grade("R", "b", "c", [])
        self.assertEqual(r.reward, 0.0)
        self.assertIn("boom", r.reason)

    def test_empty_candidate_is_rejected(self) -> None:
        g = RewardGrader(FakeVerifier(report=_report(True, 1, 0)))
        self.assertEqual(g.grade("R", "b", "   ", []).reward, 0.0)

    def test_divergences_are_reported_for_audit(self) -> None:
        d = Divergence(kind="global", ref="^DPT(3,0)", expected="A", actual="B")
        r = self._grade(_report(False, 1, 1, divs=[d]))
        self.assertIn("^DPT(3,0)", r.divergences[0])

    def test_unknown_shaping_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RewardGrader(FakeVerifier(), shaping="vibes")


if __name__ == "__main__":
    unittest.main()
