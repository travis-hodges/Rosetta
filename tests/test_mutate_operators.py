"""Tests for the eight mutation operators.

Two kinds of test here. The first kind pins each operator's behaviour on a
small hand-written fixture. The second kind is the one that matters: negative
tests that a mutation is *not* produced where it would break the source --
inside a string literal, on a ``SET`` assignment ``=``, on a ``FOR`` control
variable, in a ``WRITE`` format list, or in a way that orphans a dot block.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from rosetta.mutate.lex import MASK, TPCommandError, has_tp_command, parse_routine
from rosetta.mutate.operators import (
    OPERATOR_NAMES,
    arg_order,
    boundary,
    cmp_flip,
    dollar_misuse,
    mutate_routine,
    naked_ref,
    postcond,
    stmt_drop,
    value_spans,
    var_swap,
)

ROUTINE_DIR = Path(__file__).resolve().parent.parent / "data" / "routines"
SHORTLIST = ("PRCHUEI", "XLFSTR", "XLFCRC", "RGUTUU", "XLFDT")


def routine(src: str, name: str = "T"):
    return parse_routine(name, src)


def lines_of(muts) -> list[str | None]:
    return [m.mutated_line for m in muts]


class CmpFlipTests(unittest.TestCase):
    def test_flips_a_comparison_in_an_if_argument(self) -> None:
        muts = list(cmp_flip(routine("T ;h\n I X>3 Q 1\n")))
        self.assertIn(" I X<3 Q 1", lines_of(muts))

    def test_equality_flip_is_easy_and_boundary_flip_is_hard(self) -> None:
        muts = {m.detail: m for m in cmp_flip(routine("T ;h\n I X>3 Q 1\n"))}
        self.assertEqual(muts["gt_to_lt"].difficulty, "medium")
        # `>` -> `'<` differs from the original only at equality.
        self.assertEqual(muts["gt_to_ge"].difficulty, "hard")
        self.assertEqual(muts["gt_to_ge"].mutated_line, " I X'<3 Q 1")

    def test_does_not_touch_a_set_assignment_equals(self) -> None:
        muts = list(cmp_flip(routine("T ;h\n S X=1\n")))
        self.assertEqual(muts, [])

    def test_does_not_touch_a_for_control_variable_equals(self) -> None:
        # `F I'=1:1:3` is a syntax error; the `=` after a FOR variable is
        # structure, not a comparison.
        for m in cmp_flip(routine("T ;h\n F I=1:1:3 S X=X+1\n")):
            self.assertNotIn("I'=", m.mutated_line or "")

    def test_does_not_touch_operators_inside_a_string_literal(self) -> None:
        muts = list(cmp_flip(routine('T ;h\n S X="a>b"\n')))
        self.assertEqual(muts, [])

    def test_does_not_touch_write_format_controls(self) -> None:
        # In `W !,"x"` the `!` is a newline, not a logical OR.
        muts = list(cmp_flip(routine('T ;h\n W !,"x",?5,"y"\n')))
        self.assertEqual(muts, [])

    def test_flips_logical_or_inside_an_if(self) -> None:
        details = {m.detail for m in cmp_flip(routine("T ;h\n I X!Y Q 1\n"))}
        self.assertIn("or_to_and", details)

    def test_pattern_codes_are_not_mistaken_for_operators(self) -> None:
        muts = list(cmp_flip(routine("T ;h\n I X?3N1A Q 1\n")))
        # Only the `?` itself flips; the N/A codes must be left alone.
        self.assertEqual([m.detail for m in muts], ["pat_to_npat"])
        self.assertEqual(muts[0].mutated_line, " I X'?3N1A Q 1")


class BoundaryTests(unittest.TestCase):
    def test_shifts_an_integer_piece_index(self) -> None:
        got = lines_of(boundary(routine('T ;h\n S Y=$P(X,"^",2)\n')))
        self.assertIn(' S Y=$P(X,"^",3)', got)
        self.assertIn(' S Y=$P(X,"^",1)', got)

    def test_appends_to_an_expression_index(self) -> None:
        got = lines_of(boundary(routine("T ;h\n S Y=$E(X,I)\n")))
        self.assertIn(" S Y=$E(X,I+1)", got)
        self.assertIn(" S Y=$E(X,I-1)", got)

    def test_never_produces_a_negative_literal_index(self) -> None:
        for m in boundary(routine("T ;h\n S Y=$E(X,0)\n")):
            self.assertNotIn("-1", m.mutated_line or "")

    def test_range_end_shift_is_rated_hard(self) -> None:
        muts = {m.detail: m for m in boundary(routine("T ;h\n S Y=$E(X,1,5)\n"))}
        self.assertEqual(muts["e_range_end_minus1"].difficulty, "hard")
        self.assertEqual(muts["e_range_start_plus1"].difficulty, "medium")

    def test_ignores_a_piece_inside_a_string_literal(self) -> None:
        self.assertEqual(list(boundary(routine('T ;h\n S Y="$E(X,2)"\n'))), [])


class StmtDropTests(unittest.TestCase):
    def test_drops_a_whole_set_line(self) -> None:
        src = "T ;h\n S X=1\n S Y=2\n Q\n"
        muts = [m for m in stmt_drop(routine(src)) if m.mutated_line is None]
        self.assertEqual({m.lineno for m in muts}, {2, 3})

    def test_will_not_orphan_a_dot_block(self) -> None:
        # Line 3 is the only body of the argumentless DO on line 2. Deleting it
        # leaves an empty block, which will not load.
        src = "T ;h\n F I=1:1:3 D \n . S X=X+1\n Q\n"
        for m in stmt_drop(routine(src)):
            self.assertNotEqual((m.lineno, m.mutated_line), (3, None))

    def test_drops_one_line_of_a_multi_line_dot_block(self) -> None:
        src = "T ;h\n F I=1:1:3 D \n . S X=X+1\n . S Y=Y+1\n Q\n"
        dropped = {m.lineno for m in stmt_drop(routine(src)) if m.mutated_line is None}
        self.assertEqual(dropped, {3, 4})

    def test_inline_drop_keeps_a_following_command(self) -> None:
        got = lines_of(stmt_drop(routine("T ;h\n S X=1 S Y=2 W Y\n")))
        self.assertIn(" S Y=2 W Y", got)

    def test_never_leaves_a_trailing_for_with_nothing_to_control(self) -> None:
        # Dropping the only command after a FOR would strand the loop.
        for m in stmt_drop(routine("T ;h\n F I=1:1:3 S X=X+1\n Q\n")):
            self.assertNotEqual((m.mutated_line or "").strip(), "F I=1:1:3")

    def test_reassigned_target_is_rated_hard(self) -> None:
        src = "T ;h\nGO ;\n S X=1\n S X=2\n Q X\n"
        muts = {m.lineno: m for m in stmt_drop(routine(src)) if m.mutated_line is None}
        self.assertEqual(muts[3].difficulty, "hard")  # X is overwritten at line 4


class VarSwapTests(unittest.TestCase):
    SRC = "T ;h\nGO(A,B) ;\n N C\n S C=A+B\n Q C\n"

    def test_swaps_a_read_site_for_another_same_scope_local(self) -> None:
        got = lines_of(var_swap(routine(self.SRC)))
        self.assertIn(" S C=B+B", got)

    def test_never_invents_a_name(self) -> None:
        # The replacement must always come from the declared-locals pool of the
        # same label span -- a substituted name that was never declared would be
        # an undefined-variable error rather than a data-flow defect.
        muts = list(var_swap(routine(self.SRC)))
        self.assertTrue(muts)
        for m in muts:
            _from, _, to = m.detail.partition("_to_")
            self.assertIn(to, {"A", "B", "C"}, m.detail)

    def test_namespaced_locals_are_not_all_rated_hard(self) -> None:
        # VistA namespaces every local in a routine. Without stripping the
        # shared prefix, PRCI -> PRCLEN would score as a typo-level edit.
        src = "T ;h\nGO ;\n N PRCI,PRCLEN\n S PRCI=1,PRCLEN=2\n Q PRCI+PRCLEN\n"
        diffs = {m.difficulty for m in var_swap(routine(src))}
        self.assertNotEqual(diffs, {"hard"})

    def test_for_bound_swap_is_flagged_as_a_timeout_risk(self) -> None:
        src = "T ;h\nGO ;\n N I,N\n S N=3\n F I=1:1:N S X=1\n Q\n"
        risky = [m for m in var_swap(routine(src)) if m.timeout_risk]
        self.assertTrue(risky)


class NakedRefTests(unittest.TestCase):
    def test_perturbs_an_existing_naked_reference(self) -> None:
        src = 'T ;h\n S X=$G(^DPT(1,0)),Y=$P(^(3),"^")\n'
        got = lines_of(naked_ref(routine(src)))
        self.assertTrue(any("^(4)" in (g or "") for g in got))
        self.assertTrue(any("^(2)" in (g or "") for g in got))

    def test_introduces_a_naked_reference_after_a_prior_global(self) -> None:
        src = "T ;h\nGO ;\n S X=^DPT(2,0)\n S Y=^DPT(1,3)\n Q\n"
        muts = [m for m in naked_ref(routine(src)) if m.detail == "introduce"]
        self.assertIn(" S Y=^(3)", lines_of(muts))

    def test_does_not_introduce_one_with_no_prior_global_reference(self) -> None:
        src = "T ;h\nGO ;\n S Y=^DPT(1,3)\n Q\n"
        self.assertEqual(
            [m for m in naked_ref(routine(src)) if m.detail == "introduce"], []
        )

    def test_ignores_an_entry_reference(self) -> None:
        self.assertEqual(list(naked_ref(routine("T ;h\n D TAG^ROU(1,2)\n"))), [])


class DollarMisuseTests(unittest.TestCase):
    def test_data_becomes_get(self) -> None:
        self.assertIn(" I $G(X) Q 1", lines_of(dollar_misuse(routine("T ;h\n I $D(X) Q 1\n"))))

    def test_get_becomes_data(self) -> None:
        self.assertIn(" S Y=$D(X)", lines_of(dollar_misuse(routine("T ;h\n S Y=$G(X)\n"))))

    def test_two_argument_get_never_becomes_data(self) -> None:
        # `$D(X,0)` is not valid MUMPS and would fail to load.
        for m in dollar_misuse(routine('T ;h\n S Y=$G(X,"d")\n')):
            self.assertNotIn("$D(", m.mutated_line or "")

    def test_two_argument_get_drops_its_default(self) -> None:
        muts = {m.detail: m for m in dollar_misuse(routine('T ;h\n S Y=$G(X,"d")\n'))}
        self.assertEqual(muts["get_drop_default"].mutated_line, " S Y=$G(X)")
        self.assertEqual(muts["get_drop_default"].difficulty, "hard")

    def test_order_gains_a_reverse_direction(self) -> None:
        got = lines_of(dollar_misuse(routine("T ;h\n S Y=$O(^A(X))\n")))
        self.assertIn(" S Y=$O(^A(X),-1)", got)

    def test_order_direction_flips(self) -> None:
        got = lines_of(dollar_misuse(routine("T ;h\n S Y=$O(^A(X),-1)\n")))
        self.assertIn(" S Y=$O(^A(X),1)", got)


class PostcondTests(unittest.TestCase):
    def test_drops_a_command_postconditional(self) -> None:
        self.assertIn(" S Y=1", lines_of(postcond(routine("T ;h\n S:X>3 Y=1\n"))))

    def test_inverts_a_command_postconditional_with_balanced_parens(self) -> None:
        muts = {m.detail: m for m in postcond(routine("T ;h\n S:X>3 Y=1\n"))}
        self.assertEqual(muts["invert_set"].mutated_line, " S:'(X>3) Y=1")

    def test_drops_an_argument_level_postconditional(self) -> None:
        src = "T ;h\n D CURRDD:'$D(ARY)\n"
        muts = [m for m in postcond(routine(src)) if m.detail == "drop_argument_level"]
        self.assertEqual([m.mutated_line for m in muts], [" D CURRDD"])
        self.assertEqual(muts[0].difficulty, "hard")

    def test_quit_in_an_unbounded_for_is_flagged_as_a_timeout_risk(self) -> None:
        src = "T ;h\nGO ;\n F I=1:1 D  Q:X>3\n . S X=X+1\n Q\n"
        risky = [m for m in postcond(routine(src)) if m.timeout_risk]
        self.assertTrue(risky)

    def test_quit_in_a_bounded_for_is_not_flagged(self) -> None:
        src = "T ;h\nGO ;\n F I=1:1:9 D  Q:X>3\n . S X=X+1\n Q\n"
        self.assertEqual([m for m in postcond(routine(src)) if m.timeout_risk], [])


class ArgOrderTests(unittest.TestCase):
    def test_swaps_extrinsic_arguments(self) -> None:
        got = lines_of(arg_order(routine("T ;h\n S X=$$ADD^MATH(A,B)\n")))
        self.assertIn(" S X=$$ADD^MATH(B,A)", got)

    def test_swaps_do_call_arguments(self) -> None:
        got = lines_of(arg_order(routine("T ;h\n D FTEXT^R(78,.CMNT)\n")))
        self.assertIn(" D FTEXT^R(.CMNT,78)", got)

    def test_flags_a_by_reference_mismatch(self) -> None:
        muts = list(arg_order(routine("T ;h\n D FTEXT^R(78,.CMNT)\n")))
        self.assertIn("by-reference", muts[0].note)

    def test_single_argument_call_yields_nothing(self) -> None:
        self.assertEqual(list(arg_order(routine("T ;h\n S X=$$UP^XLFSTR(A)\n"))), [])

    def test_identical_arguments_are_not_swapped(self) -> None:
        self.assertEqual(list(arg_order(routine("T ;h\n D GO^R(A,A)\n"))), [])


class ValueSpanTests(unittest.TestCase):
    def test_write_arguments_are_not_a_value_span(self) -> None:
        line = parse_routine("T", 'T ;h\n W !,"x"\n').lines[1]
        self.assertEqual(value_spans(line), [])

    def test_set_right_hand_side_is_a_value_span(self) -> None:
        line = parse_routine("T", "T ;h\n S X=A>B\n").lines[1]
        raw = line.raw
        self.assertEqual([raw[s:e] for s, e in value_spans(line)], ["A>B"])


class SafetyTests(unittest.TestCase):
    """The constraint that must never regress."""

    def test_mutate_routine_rejects_a_mutant_carrying_a_tp_command(self) -> None:
        # No operator can introduce a command verb, so this is provoked by
        # planting one in the baseline; the guard must still fire.
        with self.assertRaises(TPCommandError):
            mutate_routine(routine("T ;h\n TSTART\n S X=1 S Y=2\n"))

    @unittest.skipUnless(ROUTINE_DIR.is_dir(), "data/routines not extracted")
    def test_no_mutant_of_the_shortlist_contains_a_tp_command(self) -> None:
        for name in SHORTLIST:
            src = (ROUTINE_DIR / f"{name}.m").read_text(errors="replace")
            for m in mutate_routine(parse_routine(name, src)):
                self.assertFalse(
                    has_tp_command(m.mutated_src),
                    f"{name} {m.key} emitted a TP command",
                )


@unittest.skipUnless(ROUTINE_DIR.is_dir(), "data/routines not extracted")
class ShortlistTests(unittest.TestCase):
    """Structural sanity over real routines, for every operator."""

    def test_every_global_free_operator_fires_on_the_shortlist(self) -> None:
        # The shortlist is deliberately made of pure, global-free routines, so
        # NAKED_REF has nothing to work with there; the other seven must all
        # produce mutants or an operator has silently stopped matching.
        seen: set[str] = set()
        for name in SHORTLIST:
            src = (ROUTINE_DIR / f"{name}.m").read_text(errors="replace")
            seen |= {m.operator for m in mutate_routine(parse_routine(name, src))}
        self.assertEqual(set(OPERATOR_NAMES) - {"NAKED_REF"} - seen, set())

    def test_every_mutant_reparses_and_stays_balanced(self) -> None:
        for name in SHORTLIST:
            src = (ROUTINE_DIR / f"{name}.m").read_text(errors="replace")
            r = parse_routine(name, src)
            for m in mutate_routine(r):
                if m.mutated_line is None:
                    continue
                self.assertEqual(m.mutated_line.count('"') % 2, 0, m.key)
                reparsed = parse_routine(name, m.mutated_src)
                self.assertEqual(len(reparsed.lines), len(r.lines), m.key)

    def test_a_deletion_removes_exactly_one_line(self) -> None:
        src = (ROUTINE_DIR / "PRCHUEI.m").read_text(errors="replace")
        r = parse_routine("PRCHUEI", src)
        for m in mutate_routine(r, ["STMT_DROP"]):
            if m.mutated_line is None:
                self.assertEqual(
                    len(m.mutated_src.splitlines()), len(r.lines) - 1, m.key
                )

    def test_mutation_only_ever_changes_one_line(self) -> None:
        src = (ROUTINE_DIR / "XLFSTR.m").read_text(errors="replace")
        r = parse_routine("XLFSTR", src)
        base = src.splitlines()
        for m in mutate_routine(r):
            got = m.mutated_src.splitlines()
            differing = [i for i, (a, b) in enumerate(zip(base, got)) if a != b]
            if m.mutated_line is None:
                self.assertEqual(len(got), len(base) - 1, m.key)
            else:
                self.assertEqual(len(got), len(base), m.key)
                self.assertEqual(differing, [m.lineno - 1], m.key)


@unittest.skipUnless(ROUTINE_DIR.is_dir(), "data/routines not extracted")
class CompilerFindingsTests(unittest.TestCase):
    """Regressions for the 26 defective mutants found by a real YottaDB compile.

    Every case names the routine and line the defect was found at. Reconstructed
    by compiling 206 generated mutants against YottaDB r2.06 in the
    ``rosetta-verify`` container: 1 hard syntax error, 16 structurally
    degenerate mutants (infinite loops, dead code) and 9 no-ops.
    """

    _cache: dict[str, list] = {}

    @classmethod
    def muts(cls, name: str, lineno: int | None = None, operator: str | None = None):
        if name not in cls._cache:
            src = (ROUTINE_DIR / f"{name}.m").read_text(errors="replace")
            cls._cache[name] = mutate_routine(parse_routine(name, src))
        return [
            m
            for m in cls._cache[name]
            if (lineno is None or m.lineno == lineno)
            and (operator is None or m.operator == operator)
        ]

    def details(self, name: str, lineno: int, operator: str) -> set[str]:
        return {m.detail for m in self.muts(name, lineno, operator)}

    # --- 1. a naked reference is not a legal lock resource name -----------

    def test_naked_ref_never_targets_a_lock_argument(self) -> None:
        # PRCPOENU line 27: `L +^PRCP(445,SDA,1):5` -> `L +^(1):5` is
        # %YDB-E-LKNAMEXPECTED at column 82. The only hard syntax error found.
        self.assertEqual(self.details("PRCPOENU", 27, "NAKED_REF"), set())
        for m in self.muts("PRCPOENU", operator="NAKED_REF"):
            self.assertNotIn("L +^(", m.mutated_line or "")

    def test_lock_arguments_are_not_naked_ref_targets_in_any_form(self) -> None:
        for form in ("L +^X(1,2)", "L -^X(1,2)", "L ^X(1,2)", "LOCK +^X(1,2)"):
            src = f"T ;h\nGO ;\n S Y=^DPT(9,0)\n{form}\n Q\n"
            self.assertEqual(
                [m for m in naked_ref(routine(src)) if m.lineno == 4], [], form
            )

    # --- 2. POSTCOND must not delete a control-flow edge ------------------

    def test_never_drops_a_goto_postconditional(self) -> None:
        # `G DOUB:D` -> `G DOUB` jumps backwards: an infinite loop that hangs
        # the harness. Same shape in ESPSOUN, which is a namespace copy.
        for name in ("XUA4A71", "ESPSOUN"):
            for lineno in (19, 24):
                self.assertNotIn(
                    "drop_argument_level",
                    self.details(name, lineno, "POSTCOND"),
                    f"{name} L{lineno}",
                )
        # PRCAFN1 8/9: `G NULL:'Y` -> `G NULL` makes the routine tail dead.
        for lineno in (8, 9):
            self.assertNotIn(
                "drop_argument_level", self.details("PRCAFN1", lineno, "POSTCOND")
            )

    def test_never_drops_a_for_terminating_quit_postconditional(self) -> None:
        # `F  S V=$O(..) Q:cond  D` -> `... Q  D`: the dot block never runs.
        for name, lineno in (("IBBACDM", 16), ("IBCRU4", 76), ("PXRMEXED", 24)):
            first = [
                m
                for m in self.muts(name, lineno, "POSTCOND")
                if m.detail == "drop_quit"
            ]
            for m in first:
                # only the trailing quit, which has nothing after it, may go
                self.assertTrue(
                    (m.mutated_line or "").rstrip().endswith(" Q"),
                    f"{name} L{lineno}: {m.mutated_line}",
                )

    def test_never_drops_a_quit_that_guards_a_live_tail(self) -> None:
        for name, lineno in (("VPSMRAR4", 23), ("YSASCSA", 27), ("SCTMAPI1", 37)):
            self.assertNotIn(
                "drop_quit", self.details(name, lineno, "POSTCOND"), f"{name} L{lineno}"
            )

    def test_still_inverts_every_one_of_those_postconditionals(self) -> None:
        # invert_* was 100% clean in the compile sweep; it must not regress.
        for name, lineno in (
            ("VPSMRAR4", 23), ("YSASCSA", 27), ("SCTMAPI1", 37),
            ("IBBACDM", 16), ("IBCRU4", 76), ("PXRMEXED", 24),
        ):
            self.assertIn(
                "invert_quit", self.details(name, lineno, "POSTCOND"), f"{name} L{lineno}"
            )

    def test_a_quit_ending_a_bounded_for_may_still_lose_its_postconditional(self) -> None:
        # LEXAS2 68: `F LEXP=LEXM:-1:1 Q:cond` -- the quit ends the loop, not
        # the block, the loop is bounded, and line 69 still runs. Legitimate.
        self.assertIn("drop_quit", self.details("LEXAS2", 68, "POSTCOND"))

    def test_a_bare_quit_at_the_end_of_a_block_is_not_a_mutation(self) -> None:
        src = "T ;h\nGO ;\n S X=1\n Q:X>3\nNEXT ;\n Q\n"
        self.assertNotIn("drop_quit", {m.detail for m in postcond(routine(src))})

    # --- 3. STMT_DROP must not freeze a loop ------------------------------

    def test_never_drops_the_order_advance_of_an_unbounded_for(self) -> None:
        for name, lineno in (
            ("PXRMEXED", 24), ("PXRMEXED", 26), ("IBDF10A", 72),
            ("IBBACDM", 16), ("IBCRU4", 76),
        ):
            for m in self.muts(name, lineno, "STMT_DROP"):
                self.assertIn(
                    "$O(", m.mutated_line or "", f"{name} L{lineno}: advance deleted"
                )

    def test_never_drops_a_by_reference_advance_from_a_loop_body(self) -> None:
        # ECOBMC 64-66: `F  Q:+CHILD=-1  D` / `.D METHOD(.CHILD,...)`.
        self.assertEqual(self.muts("ECOBMC", 66, "STMT_DROP"), [])
        # ...but a line in the same block that only *reads* CHILD is still fair
        # game, so the guard has not simply disabled the operator.
        self.assertTrue(self.muts("ECOBMC", operator="STMT_DROP"))

    def test_never_drops_a_set_that_feeds_a_backward_goto(self) -> None:
        # XUA4A71 24: `S E=... G C:E[F Q` -- delete the SET and `E` is frozen.
        self.assertEqual(self.muts("XUA4A71", 24, "STMT_DROP"), [])

    def test_a_loop_initialiser_before_the_for_is_still_droppable(self) -> None:
        drops = [
            m.note for m in self.muts("IBDF10A", 72, "STMT_DROP")
        ]
        self.assertTrue(any('S LINE=""' in n for n in drops), drops)

    # --- 4. NAKED_REF needs a knowable, distinguishable context -----------

    def test_no_naked_ref_without_a_dominating_global_reference(self) -> None:
        # FSCXREFO 41 sits three dot levels inside two IFs; nothing dominates
        # it, so `^(120)` resolves against whatever FileMan last touched.
        for m in self.muts("FSCXREFO", 41, "NAKED_REF"):
            self.assertNotIn("^(120)", m.mutated_line or "")

    def test_no_naked_ref_when_the_context_prefix_is_identical(self) -> None:
        # `^DIZ(580431.04,DA(1),"S")` after `^DIZ(580431.04,DA(1),0)`:
        # `^("S")` is byte-identical at run time, so the mutant is unkillable.
        self.assertEqual(self.muts("AJK1UBDD", 50, "NAKED_REF"), [])
        self.assertEqual(self.muts("IBDF10A", 62, "NAKED_REF"), [])
        src = "T ;h\nGO ;\n S X=^DPT(1,0)\n S Y=^DPT(1,3)\n Q\n"
        self.assertEqual(
            [m for m in naked_ref(routine(src)) if m.detail == "introduce"], []
        )

    def test_no_naked_ref_behind_an_intervening_call(self) -> None:
        # PRSAOTT 137 is reached only through `$$OTREQ`/`$$OTAPPR`, which run
        # arbitrary global references in another frame.
        self.assertEqual(self.muts("PRSAOTT", 137, "NAKED_REF"), [])

    def test_no_naked_ref_that_discards_a_for_control_variable(self) -> None:
        # ZZGENAPT 31: `F NEWAPPTID=1:1 I '$D(^SC(..,NEWAPPTID,0)) Q` -- the
        # naked form is loop-invariant, so the loop never terminates.
        self.assertEqual(self.muts("ZZGENAPT", 31, "NAKED_REF"), [])

    # --- 5. BOUNDARY must not emit a clamped range start ------------------

    def test_never_shifts_a_literal_range_start_below_one(self) -> None:
        # $EXTRACT and $PIECE clamp a start below 1 up to 1, so `$E(x,0,4)` is
        # `$E(x,1,4)`: unkillable. Verified against YottaDB r2.06.
        for name, lineno in (("LEXAS2", 68), ("PRSAENT1", 105)):
            for m in self.muts(name, lineno, "BOUNDARY"):
                self.assertNotIn(",0,", m.mutated_line or "", f"{name} L{lineno}")
            self.assertIn(
                "e_range_start_plus1", self.details(name, lineno, "BOUNDARY")
            )

    def test_no_mutant_in_the_corpus_extracts_from_a_zero_range_start(self) -> None:
        pattern = re.compile(r"\$(?:E|EXTRACT|P|PIECE)\([^()]*,0,", re.IGNORECASE)
        for name in ("LEXAS2", "PRSAENT1", "IBDF10A", "PRCHUEI", "XLFSTR"):
            for m in self.muts(name, operator="BOUNDARY"):
                self.assertIsNone(pattern.search(m.mutated_line or ""), m.key)

    # --- 6. dropping a $GET default needs a reaching-definition check -----

    def test_never_drops_a_get_default_that_cannot_be_reached(self) -> None:
        # LRPXAPI 52: lines 48/49 are an I/E pair that both assign MAX.
        self.assertNotIn(
            "get_drop_default", self.details("LRPXAPI", 52, "DOLLAR_MISUSE")
        )
        # LRPXAPI 67: `S ITEM=$G(ITEM,0)` -- "" and 0 take the identical path.
        self.assertNotIn(
            "get_drop_default", self.details("LRPXAPI", 67, "DOLLAR_MISUSE")
        )

    def test_an_empty_string_default_is_never_dropped(self) -> None:
        muts = dollar_misuse(routine('T ;h\n S Y=$G(X,"")\n'))
        self.assertNotIn("get_drop_default", {m.detail for m in muts})

    def test_a_reachable_default_is_still_dropped(self) -> None:
        muts = dollar_misuse(routine('T ;h\nGO(A) ;\n S Y=$G(B,"d")\n'))
        self.assertIn("get_drop_default", {m.detail for m in muts})

    # --- notes feed the published benchmark record ------------------------

    def test_a_postcond_note_quotes_the_raw_source_not_the_masked_line(self) -> None:
        src = 'T ;h\n Q:%1["T" 1\n'
        notes = {m.detail: m.note for m in postcond(routine(src))}
        self.assertEqual(notes["invert_quit"], 'inverted :%1["T"')

    def test_no_note_leaks_the_string_mask_character(self) -> None:
        for name in ("PRSAENT1", "IBDF10A", "LRPXAPI", "XUA4A71", "PRCPOENU"):
            for m in self.muts(name):
                self.assertNotIn(MASK, m.note, m.key)

    def test_reassignment_is_judged_per_node_not_per_base_name(self) -> None:
        # `S P(5)=0` does not overwrite `S P(3)=...`; claiming it did was a
        # false provenance record on AJK1UBDD 50.
        for m in self.muts("AJK1UBDD", 50, "STMT_DROP"):
            self.assertNotIn("reassigned later", m.note)
        src = "T ;h\nGO ;\n S P(3)=1\n S P(3)=2\n Q\n"
        dropped = [m for m in stmt_drop(routine(src)) if m.lineno == 3]
        self.assertTrue(any("reassigned later" in m.note for m in dropped))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
