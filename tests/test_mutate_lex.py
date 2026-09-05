"""Parsing tests for rosetta.mutate.lex.

This is where the mutation generator breaks if it breaks: MUMPS lines carry
string literals full of operator characters, argumentless commands marked only
by a second space, syntactically meaningful leading dots, and pattern operands
whose bare letters are not variables. Each of those gets a test here.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from rosetta.mutate.lex import (
    TPCommandError,
    assert_no_tp_command,
    declared_locals,
    find_call_spans,
    has_tp_command,
    identifier_occurrences,
    parse_line,
    parse_routine,
    scan_pattern,
    splice,
    split_top_spans,
)

ROUTINE_DIR = Path(__file__).resolve().parent.parent / "data" / "routines"


class OffsetTests(unittest.TestCase):
    """The masked line and the raw line must stay offset-identical."""

    def test_masked_line_has_same_length_as_raw(self) -> None:
        raw = ' S X="a ; b (c)",Y=1 ;trailing'
        line = parse_line(raw, 1)
        self.assertEqual(len(line.masked), len(raw))

    def test_split_top_spans_offsets_index_the_raw_line(self) -> None:
        text = "A(1,2),B,C"
        for part, s, e in split_top_spans(text, ",", base=5):
            self.assertEqual(part, ("     " + text)[s:e])

    def test_command_spans_index_the_raw_line(self) -> None:
        raw = " S:X>3 Y=1 W Y"
        line = parse_line(raw, 1)
        first = line.commands[0]
        self.assertEqual(raw[slice(*first.verb_span)], "S")
        self.assertEqual(raw[slice(*first.postcond_span)], ":X>3")  # type: ignore[misc]
        self.assertEqual(raw[slice(*first.args_span)], "Y=1")  # type: ignore[misc]

    def test_splice_rejects_an_out_of_range_span(self) -> None:
        with self.assertRaises(ValueError):
            splice("abc", 0, 99, "x")


class LineStructureTests(unittest.TestCase):
    def test_label_and_formals_in_column_one(self) -> None:
        line = parse_line("VALIDUEI(PRCSTR) ; comment", 1)
        self.assertEqual(line.label, "VALIDUEI")
        self.assertEqual(line.formals, ("PRCSTR",))

    def test_leading_whitespace_means_no_label(self) -> None:
        self.assertIsNone(parse_line(" S X=1", 1).label)

    def test_dot_level_counts_block_nesting(self) -> None:
        self.assertEqual(parse_line(" . . S X=1", 1).dot_level, 2)
        self.assertEqual(parse_line(" ..S X=1", 1).dot_level, 2)
        self.assertEqual(parse_line(" S X=1", 1).dot_level, 0)

    def test_argumentless_command_is_marked_by_a_second_space(self) -> None:
        cmds = parse_line(" F I=1:1 D  Q:X>3", 1).commands
        self.assertEqual([c.canon for c in cmds], ["FOR", "DO", "QUIT"])
        self.assertIsNone(cmds[1].args)  # argumentless DO opens a block
        self.assertEqual(cmds[2].postcond, "X>3")

    def test_string_literal_does_not_split_commands(self) -> None:
        cmds = parse_line(' S X="a b c" W X', 1).commands
        self.assertEqual([c.canon for c in cmds], ["SET", "WRITE"])
        self.assertEqual(cmds[0].args, 'X="\x01\x01\x01\x01\x01"')

    def test_semicolon_inside_a_literal_is_not_a_comment(self) -> None:
        line = parse_line(' S X="a;b" S Y=2', 1)
        self.assertEqual([c.canon for c in line.commands], ["SET", "SET"])

    def test_comment_is_excluded_from_the_code_span(self) -> None:
        line = parse_line(" S X=1 ; S Y=2", 1)
        self.assertEqual([c.canon for c in line.commands], ["SET"])

    def test_numeric_label_line_yields_no_commands(self) -> None:
        # `1 ;;11431` is a data line in LA7SBC, not executable code.
        self.assertEqual(parse_line("1 ;;11431", 1).commands, ())

    def test_label_spans_cover_the_whole_routine(self) -> None:
        r = parse_routine("T", "T ;h\nA(X) ;\n Q X\nB ;\n Q 1\n")
        self.assertEqual(r.label_spans["A"], (2, 3))
        self.assertEqual(r.label_spans["B"], (4, 5))
        self.assertEqual(r.label_at(3), "A")


class PatternTests(unittest.TestCase):
    """Pattern operands contain bare letters that are codes, not variables."""

    def test_scan_pattern_consumes_a_simple_pattern(self) -> None:
        text = "X?12UN Q"
        self.assertEqual(scan_pattern(text, 2), len("X?12UN"))

    def test_scan_pattern_consumes_literals_inside_a_pattern(self) -> None:
        text = 'X?3N1"-"4N'
        self.assertEqual(scan_pattern(text, 2), len(text))

    def test_scan_pattern_handles_a_repetition_range(self) -> None:
        self.assertEqual(scan_pattern("X?1.3N", 2), len("X?1.3N"))

    def test_scan_pattern_returns_start_when_nothing_matches(self) -> None:
        self.assertEqual(scan_pattern("X?", 2), 2)


class IdentifierTests(unittest.TestCase):
    """Scanned over value spans, as the operators do.

    Value spans exclude command verbs and, for ``SET``, the assignment target:
    ``VAR_SWAP`` rewrites read sites only, never a write site.
    """

    @staticmethod
    def _names(raw: str) -> set[str]:
        from rosetta.mutate.operators import value_spans

        line = parse_line(raw, 1)
        return {
            o.name
            for s, e in value_spans(line)
            for o in identifier_occurrences(line.masked, s, e)
        }

    def test_globals_and_intrinsics_are_not_identifiers(self) -> None:
        names = self._names(" S X=$G(^DPT(1,0))+$L(Y)")
        self.assertEqual(names, {"Y"})  # ^DPT, $G, $L excluded; X is the target

    def test_entryref_tag_and_routine_are_not_identifiers(self) -> None:
        names = self._names(" S X=$$UEICHK^PRCHUEI(Z)")
        self.assertEqual(names, {"Z"})  # neither UEICHK nor PRCHUEI is a local

    def test_pattern_codes_are_not_identifiers(self) -> None:
        names = self._names(" I PRCSTR'?12UN Q 0")
        self.assertNotIn("UN", names)
        self.assertIn("PRCSTR", names)

    def test_string_content_is_not_an_identifier(self) -> None:
        names = self._names(' S X="HELLO"')
        self.assertEqual(names, set())


class DeclaredLocalsTests(unittest.TestCase):
    SRC = (
        "T ;header\n"
        "GO(A,B) ;\n"
        " N C,D\n"
        " S C=A+B\n"
        " F I=1:1:3 S D=D+I\n"
        " Q C_D\n"
    )

    def test_formals_new_set_and_for_targets_are_declared(self) -> None:
        r = parse_routine("T", self.SRC)
        self.assertEqual(declared_locals(r, "GO"), {"A", "B", "C", "D", "I"})

    def test_scope_is_the_label_span(self) -> None:
        r = parse_routine("T", self.SRC + "OTHER ;\n S ZZZ=1\n Q\n")
        self.assertNotIn("ZZZ", declared_locals(r, "GO"))
        self.assertIn("ZZZ", declared_locals(r, "OTHER"))


class CallSiteTests(unittest.TestCase):
    def test_extrinsic_call_arguments_are_located(self) -> None:
        line = parse_line(" S X=$$ADD^MATH(A,B)", 1)
        site = find_call_spans(line)[0]
        self.assertEqual(site.kind, "extrinsic")
        self.assertEqual([a[0] for a in site.args], ["A", "B"])

    def test_do_call_with_pass_by_reference_argument(self) -> None:
        line = parse_line(" D FTEXT^ALPBFRMU(78,.CMNT,.TEXT)", 1)
        site = find_call_spans(line)[0]
        self.assertEqual(site.kind, "do")
        self.assertEqual([a[0] for a in site.args], ["78", ".CMNT", ".TEXT"])

    def test_nested_parentheses_do_not_split_arguments(self) -> None:
        line = parse_line(" S X=$$F^R($P(A,U,2),B)", 1)
        site = [s for s in find_call_spans(line) if s.kind == "extrinsic"][0]
        self.assertEqual(len(site.args), 2)


class TPCommandTests(unittest.TestCase):
    """The verifier runs candidates inside a TP frame; a TCOMMIT would commit it."""

    def test_command_position_tp_commands_are_detected(self) -> None:
        for src in (" TSTART\n", " TCOMMIT\n", " TROLLBACK\n", " TS (X)\n", " TC\n"):
            with self.subTest(src=src):
                self.assertTrue(has_tp_command(src))

    def test_tp_word_in_a_comment_is_not_a_command(self) -> None:
        self.assertFalse(has_tp_command(" S X=1 ;TCOMMIT here\n"))

    def test_tp_word_in_a_string_literal_is_not_a_command(self) -> None:
        self.assertFalse(has_tp_command(' S X="TCOMMIT"\n'))

    def test_tc_as_a_quit_argument_is_not_a_command(self) -> None:
        # FHWOR5R.m line 32 is literally `EXIT Q TC` -- QUIT returning local TC.
        self.assertFalse(has_tp_command("EXIT Q TC\n"))

    def test_substring_of_a_longer_name_is_not_a_command(self) -> None:
        # SDES2UTDT.m contains `$$DSTSTART^SDES2UTIL`, which embeds "TSTART".
        self.assertFalse(has_tp_command(' S D=$$DSTSTART^SDES2UTIL(Y,"DST")\n'))

    def test_assert_raises_on_a_tp_command(self) -> None:
        with self.assertRaises(TPCommandError):
            assert_no_tp_command(" TCOMMIT\n", "unit test")

    def test_assert_passes_on_clean_source(self) -> None:
        assert_no_tp_command(" S X=1\n", "unit test")


@unittest.skipUnless(ROUTINE_DIR.is_dir(), "data/routines not extracted")
class CorpusTests(unittest.TestCase):
    """Whole-corpus smoke: the parser must not choke on real VistA."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.files = sorted(ROUTINE_DIR.glob("*.m"))

    def test_every_routine_parses(self) -> None:
        for p in self.files:
            with self.subTest(routine=p.stem):
                parse_routine(p.stem, p.read_text(errors="replace"))

    def test_no_baseline_routine_uses_transaction_processing(self) -> None:
        # Real VistA contains zero command-position TP commands. If this ever
        # fails, a baseline is unsafe to run inside the verifier's TP frame.
        offenders = [
            p.stem
            for p in self.files
            if has_tp_command(p.read_text(errors="replace"))
        ]
        self.assertEqual(offenders, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
