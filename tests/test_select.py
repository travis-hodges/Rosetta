import unittest

from rosetta.bench.select import (
    analyse_source,
    mask_strings,
    score_routine,
    split_top,
    strip_comment,
)


class LexerTests(unittest.TestCase):
    def test_mask_strings_hides_structure_inside_literals(self) -> None:
        masked = mask_strings('S X="a ; b (c)" W X')
        self.assertNotIn(";", masked)
        self.assertNotIn("(", masked)
        self.assertEqual(len(masked), len('S X="a ; b (c)" W X'))

    def test_mask_strings_handles_escaped_quote(self) -> None:
        masked = mask_strings('S X="say ""hi"" now" S Y=1')
        self.assertEqual(masked.count('"'), 2)

    def test_strip_comment_keeps_code(self) -> None:
        self.assertEqual(strip_comment(" S X=1 ;set it").strip(), "S X=1")

    def test_split_top_ignores_separators_inside_parentheses(self) -> None:
        self.assertEqual(split_top("A(1,2),B", ","), ["A(1,2)", "B"])

    def test_split_top_preserves_empty_token_for_argumentless_command(self) -> None:
        # MUMPS marks an argumentless command with a second space.
        self.assertEqual(split_top("D  W X", " "), ["D", "", "W", "X"])


class AnalyseSourceTests(unittest.TestCase):
    def test_labels_and_formal_arguments(self) -> None:
        facts = analyse_source("T", "T ;header\nADD(A,B) ;sum\n Q A+B\nNOARG ;\n Q 1\n")
        labels = {l["label"]: l["formals"] for l in facts["labels"]}
        self.assertEqual(labels["ADD"], ["A", "B"])
        self.assertEqual(labels["NOARG"], [])
        self.assertEqual(facts["extrinsic_entries"], ["ADD"])

    def test_global_reads_and_writes_are_separated(self) -> None:
        facts = analyse_source(
            "T", "T ;h\nGO(X) ;\n S ^DPT(X,0)=$G(^AUPNVSIT(X))\n K ^XTMP(X)\n Q 1\n"
        )
        self.assertEqual(facts["globals_read"], ["AUPNVSIT"])
        self.assertEqual(facts["globals_written"], ["DPT", "XTMP"])
        self.assertEqual(facts["global_count"], 3)

    def test_naked_references_counted_separately(self) -> None:
        facts = analyse_source(
            "T", "T ;h\nGO(X) ;\n S Y=^DPT(X,0),Z=^(1)\n S ^(2)=5\n Q Y\n"
        )
        self.assertEqual(facts["naked_read"], 1)
        self.assertEqual(facts["naked_write"], 1)
        self.assertEqual(facts["globals_read"], ["DPT"])

    def test_routine_calls_from_do_goto_and_extrinsic(self) -> None:
        src = "T ;h\nGO(X) ;\n D EN^ONE\n G ^TWO\n S Y=$$F^THREE(X)\n Q Y\n"
        facts = analyse_source("T", src)
        self.assertEqual(facts["calls"], ["ONE", "THREE", "TWO"])
        self.assertEqual(facts["fanout"], 3)
        self.assertNotIn("ONE", facts["globals_read"])

    def test_entryref_postconditional_is_not_a_global(self) -> None:
        # `D SETDPT^DPTLK1:Y>0` — the trailing :cond must not derail the parse.
        facts = analyse_source("T", "T ;h\nGO(X) ;\n D SETDPT^DPTLK1:X>0\n Q 1\n")
        self.assertEqual(facts["calls"], ["DPTLK1"])
        self.assertEqual(facts["globals_read"], [])

    def test_text_reference_is_a_routine_not_a_global(self) -> None:
        facts = analyse_source("T", 'T ;h\nGO(X) ;\n I $T(PATIENT^MPIF)\'="" Q 1\n Q 0\n')
        self.assertIn("MPIF", facts["calls"])
        self.assertEqual(facts["globals_read"], [])

    def test_justify_is_not_mistaken_for_process_id(self) -> None:
        # $J(...) is $JUSTIFY and deterministic; bare $J is the process id.
        pure = analyse_source("T", 'T ;h\nGO(X) ;\n Q $J("",X)\n')
        self.assertEqual(pure["nondeterminism"], [])
        impure = analyse_source("T", "T ;h\nGO(X) ;\n Q $J\n")
        self.assertEqual(impure["nondeterminism"], ["$J"])

    def test_line_counts(self) -> None:
        facts = analyse_source("T", "T ;h\n ;just a comment\nGO(X) ;\n Q X\n")
        self.assertEqual(facts["lines_total"], 4)
        self.assertEqual(facts["lines_code"], 1)

    def test_nondeterminism_is_attributed_to_the_label_that_causes_it(self) -> None:
        # The XLFDT shape: pure conversion entries alongside a $HOROLOG reader.
        src = "T ;h\nCONV(X) ;\n Q X+1\nNOW() ;\n Q $H\n"
        facts = analyse_source("T", src)
        self.assertEqual(facts["nondeterminism"], ["$H"])
        self.assertIn("CONV", facts["clean_extrinsic_entries"])
        self.assertNotIn("NOW", facts["clean_extrinsic_entries"])

    def test_label_flags_propagate_through_local_calls(self) -> None:
        src = "T ;h\nGO(X) ;\n D HELPER\n Q X\nHELPER ;\n R Y:5\n Q\n"
        facts = analyse_source("T", src)
        self.assertEqual(facts["clean_extrinsic_entries"], [])

    def test_entry_coverage_detects_logic_hidden_behind_bare_tags(self) -> None:
        # Two trivial formal-arg helpers; the real work sits in a tag that reads
        # caller-scope variables. This is the LEXXM2 shape.
        src = (
            "T ;h\n"
            "UP(X) ;\n Q X\n"
            "LO(X) ;\n Q X\n"
            "T2 ;\n S A=1\n S B=2\n S C=3\n S D=4\n S E=5\n S F=6\n Q\n"
        )
        facts = analyse_source("T", src)
        self.assertEqual(sorted(facts["clean_extrinsic_entries"]), ["LO", "UP"])
        self.assertLess(facts["entry_coverage"], 0.3)


class ScoringTests(unittest.TestCase):
    def _facts(self, **over: object) -> dict:
        base = {
            "fanout": 0,
            "global_count": 0,
            "globals_written": [],
            "naked_total": 0,
            "indirection": 0,
            "xecute": 0,
            "scratch_globals": [],
            "transitive_calls": 0,
            "transitive_global_count": 0,
            "commands": {},
            "extrinsic_entries": ["A"],
            "clean_extrinsic_entries": ["A"],
            "entry_coverage": 1.0,
            "lines_code": 60,
        }
        base.update(over)
        return base

    def test_fanout_and_globals_lower_is_better(self) -> None:
        clean = score_routine(self._facts(), 0.5)["score"]
        noisy = score_routine(self._facts(fanout=6, global_count=8), 0.5)["score"]
        self.assertGreater(clean, noisy)

    def test_transitive_reach_penalises_thin_wrappers(self) -> None:
        # AJETIU4: fan-out 1, no globals of its own, but its callee reads the
        # whole patient record.
        wrapper = score_routine(
            self._facts(fanout=1, transitive_calls=8, transitive_global_count=19), 0.5
        )["score"]
        genuine = score_routine(self._facts(fanout=1, transitive_calls=1), 0.5)["score"]
        self.assertGreater(genuine, wrapper)

    def test_components_sum_to_the_score(self) -> None:
        result = score_routine(self._facts(fanout=2, global_count=3), 0.4)
        self.assertAlmostEqual(sum(result["components"].values()), result["score"], places=1)


if __name__ == "__main__":
    unittest.main()
