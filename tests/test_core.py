"""Tests for rosetta.core.

Two halves. The first needs nothing but Python and covers the pure logic:
protocol escaping, TP-command rejection, extrinsic detection, capture planning,
result diffing.

The second half drives real YottaDB in the `rosetta-verify` container and is
skipped when that container is not running -- CI has no Docker. YottaDB is
never mocked: a mocked verifier proves nothing, and correctness of this module
is the whole project.

    scripts/bootstrap.sh && python -m unittest tests.test_core -v

Set ROSETTA_SLOW_TESTS=1 to include the full database snapshot/restore round trip.
"""

from __future__ import annotations

import os
import shutil
import string
import subprocess
import tempfile
import unittest
from pathlib import Path

from rosetta.core import (
    CaptureTier,
    Divergence,
    ExecResult,
    ExecSpec,
    Runtime,
    RoutineRejected,
    VerifyReport,
    WorkerError,
    diff_results,
    find_tp_commands,
    parse,
    plan_capture,
    reject_if_unsafe,
)
from rosetta.core.config import CoreConfig, DEFAULT_CONFIG, KILLED
from rosetta.core.protocol import Request, Response, escape, unescape
from rosetta.core.selftest import (
    PXVSC_MUTATION,
    UEI_CASES,
    UEI_MUTATION,
    _PX_CASES,
    _mutate,
)

REPO = Path(__file__).resolve().parents[1]
ROUTINES = REPO / "data" / "routines"


def container_running() -> bool:
    if shutil.which(DEFAULT_CONFIG.docker) is None:
        return False
    try:
        proc = subprocess.run(
            [DEFAULT_CONFIG.docker, "inspect", "-f", "{{.State.Status}}",
             DEFAULT_CONFIG.container],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.stdout.strip() == "running"


HAVE_YDB = container_running()
needs_ydb = unittest.skipUnless(
    HAVE_YDB,
    f"container {DEFAULT_CONFIG.container!r} is not running; run scripts/bootstrap.sh",
)


def read_routine(name: str) -> str:
    return (ROUTINES / f"{name}.m").read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- pure logic


class TestProtocol(unittest.TestCase):
    def test_roundtrip_preserves_everything(self) -> None:
        payloads = [
            "", "plain", "with space", "tab\there", "nl\nhere", "cr\rhere",
            "pct% and =eq", "%25 literal", "^DPT(3,0)", "a=b%c\td\ne",
            "".join(chr(c) for c in range(32, 127)),
        ]
        for value in payloads:
            with self.subTest(value=value):
                enc = escape(value)
                self.assertNotIn("\n", enc)
                self.assertNotIn("\t", enc)
                self.assertEqual(unescape(enc), value)

    def test_escaped_value_never_contains_bare_equals(self) -> None:
        self.assertNotIn("=", escape('^X("a=b")'))
        self.assertEqual(unescape(escape('^X("a=b")')), '^X("a=b")')

    def test_request_encoding_is_line_oriented(self) -> None:
        req = Request().add("CMD", "EXEC").add("ARG", "a\tb").add("ARG", "c")
        lines = req.encode().splitlines()
        self.assertEqual(lines[0], "CMD\tEXEC")
        self.assertEqual(lines[1], "ARG\ta%09b")
        self.assertEqual(lines[-1], "END")

    def test_response_multimap(self) -> None:
        r = Response.parse(["STATUS\tOK", "GR\t^X(1)", "GV\t5", "GR\t^X(2)", "GV\t6"])
        self.assertTrue(r.ok)
        self.assertEqual(r.get_all("GR"), ["^X(1)", "^X(2)"])
        self.assertEqual(r.get_all("GV"), ["5", "6"])
        self.assertEqual(r.get_int("MISSING", 7), 7)


class TestTPRejection(unittest.TestCase):
    def test_rejects_every_tp_command_and_abbreviation(self) -> None:
        for body in [
            "GO ;\n TSTART ()\n Q\n",
            "GO ;\n TCOMMIT\n Q\n",
            "GO ;\n TROLLBACK\n Q\n",
            "GO ;\n TS ():SERIAL\n Q\n",
            "GO ;\n TC\n Q\n",
            "GO ;\n . TCOMMIT\n",
            "GO ;\n D\n .TCOMMIT\n",
            "GO ;\n D\n .TC\n",
            "GO ;\n I 1 D\n . . TROLLBACK\n",
            "GO ;\n S X=1 TCOMMIT\n",
            "GO ;\n I X TROLLBACK\n",
            "GO ;\n D  TC\n",
        ]:
            with self.subTest(body=body.strip()):
                with self.assertRaises(RoutineRejected):
                    reject_if_unsafe("X", body)

    def test_does_not_reject_quit_with_a_local_named_tc(self) -> None:
        # FHWOR5R has `EXIT Q TC` -- TC is a local, not a TCOMMIT.
        self.assertEqual(find_tp_commands(read_routine("FHWOR5R")), [])
        reject_if_unsafe("FHWOR5R", read_routine("FHWOR5R"))

    def test_does_not_reject_tp_words_in_strings_or_comments(self) -> None:
        self.assertEqual(find_tp_commands('GO ;\n S X="TCOMMIT"\n'), [])
        self.assertEqual(find_tp_commands("GO ; TCOMMIT is not used here\n"), [])

    def test_whole_corpus_is_accepted(self) -> None:
        """PROJECT.md claims real VistA never uses TP. Hold it to that."""
        offenders = []
        for path in sorted(ROUTINES.glob("*.m")):
            hits = find_tp_commands(path.read_text(encoding="utf-8", errors="replace"))
            if hits:
                offenders.append((path.name, hits))
        self.assertEqual(offenders, [], "TP commands found in the corpus")

    def test_reports_the_line_number(self) -> None:
        hits = find_tp_commands("GO ;\n S X=1\n TCOMMIT\n")
        self.assertEqual(hits, [(3, "TCOMMIT")])


class TestExtrinsicDetection(unittest.TestCase):
    def test_explicit_dollar_prefix_always_wins(self) -> None:
        facts = parse("PRCHUEI", read_routine("PRCHUEI"))
        self.assertTrue(facts.is_extrinsic("$$VALIDUEI"))
        self.assertTrue(facts.is_extrinsic("$$ANYTHING"))

    def test_infers_from_formals_and_argumented_quit(self) -> None:
        facts = parse("PRCHUEI", read_routine("PRCHUEI"))
        self.assertTrue(facts.is_extrinsic("VALIDUEI"))
        self.assertTrue(facts.is_extrinsic("UEICHK"))

    def test_subroutine_with_formals_is_not_extrinsic(self) -> None:
        facts = parse("PXVSC", read_routine("PXVSC"))
        self.assertFalse(facts.is_extrinsic("SVSC"))
        self.assertFalse(facts.is_extrinsic("KVSC"))

    def test_none_entry_is_not_extrinsic(self) -> None:
        self.assertFalse(parse("PRCHUEI", read_routine("PRCHUEI")).is_extrinsic(None))

    def test_xlfcrc_entries(self) -> None:
        facts = parse("XLFCRC", read_routine("XLFCRC"))
        self.assertTrue(facts.is_extrinsic("CRC32"))
        self.assertTrue(facts.is_extrinsic("CRC16"))


class TestCapturePlanning(unittest.TestCase):
    def test_global_depth_ignores_function_arguments_and_string_commas(self) -> None:
        facts = parse("ROSDEP", 'ROSDEP\n S ^ROSSCR("a,b",$P("x,y",",",1),3)=1\n Q\n')
        self.assertEqual(facts.max_global_depth, 3)

    def test_pure_routine_needs_no_capture(self) -> None:
        plan = plan_capture(parse("PRCHUEI", read_routine("PRCHUEI")))
        self.assertEqual(plan.tier, CaptureTier.NONE)
        self.assertTrue(plan.complete)

    def test_small_root_uses_the_query_tier(self) -> None:
        facts = parse("PXVSC", read_routine("PXVSC"))
        plan = plan_capture(facts, node_counts={"^PXRMINDX": 10})
        self.assertEqual(plan.tier, CaptureTier.QUERY)
        self.assertIn("^PXRMINDX", plan.query_roots)

    def test_large_root_falls_back_to_triggers(self) -> None:
        facts = parse("PXVSC", read_routine("PXVSC"))
        plan = plan_capture(facts, node_counts={"^PXRMINDX": 500_000},
                            query_tier_node_cap=2_000)
        self.assertEqual(plan.tier, CaptureTier.TRIGGER)
        self.assertIn("^PXRMINDX", plan.trigger_roots)

    def test_unbounded_write_set_is_flagged_incomplete(self) -> None:
        src = "GO(R) ;\n S @R=1\n Q\n"
        plan = plan_capture(parse("Z", src))
        self.assertFalse(plan.complete)
        self.assertIn("unbounded", plan.reason)

    def test_reserved_globals_are_never_watched(self) -> None:
        src = "GO ;\n S ^ROSTMP(1)=1,^ROSLOG(1)=1\n Q\n"
        plan = plan_capture(parse("Z", src))
        self.assertEqual(plan.all_roots, ())

    def test_roots_get_a_caret(self) -> None:
        self.assertEqual(parse("PXVSC", read_routine("PXVSC")).globals_written,
                         ["^PXRMINDX"])


class TestDiffResults(unittest.TestCase):
    def _r(self, **kw: object) -> ExecResult:
        base = dict(stdout="", error=None, globals_out={}, duration_ms=0)
        base.update(kw)
        return ExecResult(**base)  # type: ignore[arg-type]

    def test_identical_results_do_not_diverge(self) -> None:
        a = self._r(stdout="1", globals_out={"^X(1)": "a"})
        self.assertEqual(diff_results(a, a), [])

    def test_output_divergence_names_stdout(self) -> None:
        d = diff_results(self._r(stdout="1"), self._r(stdout="0"), 3)
        self.assertEqual([(x.kind, x.ref, x.expected, x.actual, x.case_index) for x in d],
                         [("output", "stdout", "1", "0", 3)])

    def test_global_divergence_names_the_ref(self) -> None:
        d = diff_results(
            self._r(globals_out={"^DPT(3,0)": "a", "^DPT(4,0)": "same"}),
            self._r(globals_out={"^DPT(3,0)": "b", "^DPT(4,0)": "same"}),
        )
        self.assertEqual(len(d), 1)
        self.assertEqual((d[0].kind, d[0].ref, d[0].expected, d[0].actual),
                         ("global", "^DPT(3,0)", "a", "b"))

    def test_absent_and_killed_render_readably(self) -> None:
        d = diff_results(self._r(globals_out={"^X": "v"}),
                         self._r(globals_out={"^Y": KILLED}))
        by_ref = {x.ref: (x.expected, x.actual) for x in d}
        self.assertEqual(by_ref["^X"], ("v", "<absent>"))
        self.assertEqual(by_ref["^Y"], ("<absent>", "<killed>"))

    def test_an_error_is_a_divergence(self) -> None:
        d = diff_results(self._r(error=None), self._r(error="M6, undefined"))
        self.assertEqual(d[0].kind, "error")
        self.assertEqual(d[0].ref, "error")

    def test_collects_every_divergence_without_early_return(self) -> None:
        d = diff_results(
            self._r(stdout="a", error=None, globals_out={"^X": "1", "^Y": "2"}),
            self._r(stdout="b", error="M7", globals_out={"^X": "9", "^Y": "8"}),
        )
        self.assertEqual({x.kind for x in d}, {"output", "error", "global"})
        self.assertEqual(len(d), 4)


class TestVerifyReportSummary(unittest.TestCase):
    def test_equivalent_summary(self) -> None:
        r = VerifyReport(equivalent=True, divergences=[], n_cases=5, n_diverged=0)
        self.assertIn("equivalent over 5", r.summary())

    def test_divergent_summary_names_the_ref(self) -> None:
        r = VerifyReport(
            equivalent=False,
            divergences=[Divergence("global", "^DPT(3,0)", "a", "b", 1)],
            n_cases=2, n_diverged=1,
        )
        text = r.summary()
        self.assertIn("NOT equivalent", text)
        self.assertIn("^DPT(3,0)", text)


# ------------------------------------------------------------ live YottaDB


@needs_ydb
class TestLiveRuntime(unittest.TestCase):
    """Real routines, real transactions, real global state."""

    rt: Runtime

    @classmethod
    def setUpClass(cls) -> None:
        cls.rt = Runtime()
        cls.rt.worker.ensure_started()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.rt.close()

    # -- the canonical test -------------------------------------------------

    def test_cmp_flip_on_a_real_routine_is_detected(self) -> None:
        """PROJECT.md #11: if this passes, the project works."""
        baseline = read_routine("PRCHUEI")
        mutant = _mutate(baseline, *UEI_MUTATION, "PRCHUEI")
        self.assertNotEqual(baseline, mutant)

        report = self.rt.verify_equivalence("PRCHUEI", baseline, mutant, UEI_CASES)

        self.assertFalse(report.equivalent, report.summary())
        self.assertEqual(report.n_void, 0, report.summary())
        self.assertGreater(report.n_diverged, 0)
        named = [d for d in report.divergences if d.kind == "output" and d.ref == "stdout"]
        self.assertTrue(named, f"no divergence named stdout: {report.summary()}")
        self.assertEqual({(d.expected, d.actual) for d in named}, {("1", "0"), ("0", "1")})

    def test_cmp_flip_naming_a_specific_global_ref(self) -> None:
        baseline = read_routine("PXVSC")
        mutant = _mutate(baseline, *PXVSC_MUTATION, "PXVSC", occurrence=2)

        report = self.rt.verify_equivalence("PXVSC", baseline, mutant, _PX_CASES)

        self.assertFalse(report.equivalent, report.summary())
        self.assertEqual(report.n_void, 0)
        refs = {d.ref for d in report.divergences if d.kind == "global"}
        self.assertTrue(refs, report.summary())
        self.assertTrue(
            all(r.startswith("^PXRMINDX(9000010.71,") for r in refs),
            f"divergences are not specific: {refs}",
        )
        self.assertTrue(
            any('"IP","10D","Z00.00",777' in r for r in refs),
            f"expected the IP index node among {refs}",
        )

    def test_identical_source_is_equivalent(self) -> None:
        baseline = read_routine("PRCHUEI")
        report = self.rt.verify_equivalence("PRCHUEI", baseline, baseline, UEI_CASES)
        self.assertTrue(report.equivalent, report.summary())
        self.assertEqual(report.divergences, [])
        self.assertEqual(report.n_void, 0)

    def test_equivalent_mutant_is_not_flagged(self) -> None:
        """A behaviour-preserving edit must not produce a divergence.

        This is the false-positive guard. Without it a verifier that always
        said "different" would pass the canonical test.
        """
        baseline = read_routine("PRCHUEI")
        neutral = baseline.replace(
            "S PRCVALID=0", "S PRCVALID=0 ; rewritten but identical", 1
        )
        self.assertNotEqual(baseline, neutral)
        report = self.rt.verify_equivalence("PRCHUEI", baseline, neutral, UEI_CASES)
        self.assertTrue(report.equivalent, report.summary())

    # -- execution mechanics -------------------------------------------------

    def test_extrinsic_return_value_lands_in_stdout(self) -> None:
        self.rt.load_routine("XLFCRC", read_routine("XLFCRC"))
        r = self.rt.execute(ExecSpec(routine="XLFCRC", entry="$$CRC32", args=["hello"]))
        self.assertIsNone(r.error)
        self.assertEqual(r.stdout, "907060870")
        self.assertFalse(r.void)
        self.assertEqual(r.restarts, 0)

    def test_m_error_is_captured_not_raised(self) -> None:
        self.rt.load_routine("ROSTERR", "ROSTERR ;\nGO() ;\n Q ZZNOSUCHZZ\n")
        r = self.rt.execute(ExecSpec(routine="ROSTERR", entry="$$GO"))
        self.assertIsNotNone(r.error)
        self.assertIn("LVUNDEF", r.error or "")
        self.assertFalse(r.void)

    def test_device_output_is_captured(self) -> None:
        self.rt.load_routine("ROSTOUT", 'ROSTOUT ;\nGO ;\n W "line1",!,"line2"\n Q\n')
        r = self.rt.execute(ExecSpec(routine="ROSTOUT", entry="GO"))
        self.assertEqual(r.stdout, "line1\nline2")
        self.assertIsNone(r.error)

    def test_zwrite_output_is_part_of_the_verdict(self) -> None:
        baseline = 'ROSZOUT\nGO\n N X S X=1\n ZWRITE X\n Q\n'
        candidate = baseline.replace("X=1", "X=2")
        report = self.rt.verify_equivalence("ROSZOUT", baseline, candidate, [ExecSpec("ROSZOUT", "GO")])
        self.assertFalse(report.equivalent)
        self.assertEqual(report.n_void, 0)
        self.assertTrue(any(d.kind == "output" for d in report.divergences))

    def test_globals_are_rolled_back(self) -> None:
        self.rt.load_routine(
            "ROSTWR", 'ROSTWR ;\nGO ;\n S ^ROSSCR("k")="v",^ROSSCR("k",1)=1\n Q\n'
        )
        r = self.rt.execute(ExecSpec(routine="ROSTWR", entry="GO"))
        self.assertEqual(r.globals_out, {'^ROSSCR("k")': "v", '^ROSSCR("k",1)': "1"})
        residue = self.rt._sh(
            'source /home/vehu/etc/env && $gtm_dist/mumps -run %XCMD '
            '\'W $D(^ROSSCR),"/",$TLEVEL\''
        ).strip()
        self.assertEqual(residue, "0/0")

    def test_kill_is_reported_distinctly_from_empty_string(self) -> None:
        self.rt.load_routine(
            "ROSTKIL",
            'ROSTKIL ;\nGO ;\n S ^ROSSCR("a")=1,^ROSSCR("b")=""\n K ^ROSSCR("a")\n Q\n',
        )
        r = self.rt.execute(ExecSpec(routine="ROSTKIL", entry="GO"))
        self.assertEqual(r.globals_out.get('^ROSSCR("b")'), "")
        self.assertNotIn('^ROSSCR("a")', r.globals_out)

    def test_globals_in_is_seeded_and_rolled_back(self) -> None:
        self.rt.load_routine(
            "ROSTSEED", 'ROSTSEED ;\nGO ;\n S ^ROSSCR("out")=$G(^ROSSCR("in"))_"!"\n Q\n'
        )
        r = self.rt.execute(ExecSpec(
            routine="ROSTSEED", entry="GO", globals_in={'^ROSSCR("in")': "seeded"},
        ))
        self.assertEqual(r.globals_out.get('^ROSSCR("out")'), "seeded!")
        residue = self.rt._sh(
            'source /home/vehu/etc/env && $gtm_dist/mumps -run %XCMD '
            '\'W $D(^ROSSCR)\''
        ).strip()
        self.assertEqual(residue, "0")

    def test_locals_in_and_by_reference_args(self) -> None:
        self.rt.load_routine(
            "ROSTREF",
            'ROSTREF ;\nGO(A,B) ;\n S ^ROSSCR("r")=$G(A(1))_"/"_$G(A(2))_"/"_B\n Q\n',
        )
        r = self.rt.execute(ExecSpec(
            routine="ROSTREF", entry="GO",
            locals_in={"A(1)": "one", "A(2)": "two"},
            args=[".A", "three"],
        ))
        self.assertEqual(r.globals_out.get('^ROSSCR("r")'), "one/two/three")

    def test_values_with_tabs_newlines_and_percent_survive(self) -> None:
        self.rt.load_routine("ROSTESC", 'ROSTESC ;\nGO(X) ;\n S ^ROSSCR("e")=X\n Q\n')
        nasty = "a%b=c\td\ne\rf^g\"h"
        r = self.rt.execute(ExecSpec(routine="ROSTESC", entry="GO", args=[nasty]))
        self.assertEqual(r.globals_out.get('^ROSSCR("e")'), nasty)

    def test_trigger_cleanup_preserves_another_runtimes_triggers(self) -> None:
        from rosetta.core import CapturePlan
        with Runtime() as other:
            plan = CapturePlan(tier=CaptureTier.TRIGGER, trigger_roots=("^ROSSCR",))
            self.rt._sync_triggers(plan)
            other._sync_triggers(plan)
            try:
                other.clear_triggers()
                # This worker's trigger must still execute after the other
                # owner closes. Seeded watches make the log independent of
                # any pre-existing application data.
                source = 'ROSTOWN ;\nGO ;\n S ^ROSSCR("owner")="kept"\n Q\n'
                self.rt.load_routine("ROSTOWN", source)
                loaded = self.rt._loaded["ROSTOWN"]
                from dataclasses import replace
                self.rt._loaded["ROSTOWN"] = replace(loaded, plan=plan)
                self.rt._sync_triggers(plan)
                other.clear_triggers()
                result = self.rt.execute(ExecSpec("ROSTOWN", "GO"))
                self.assertEqual(result.globals_out.get('^ROSSCR("owner")'), "kept")
                self.assertFalse(result.void)
            finally:
                self.rt.clear_triggers()

    # -- failure modes -------------------------------------------------------

    def test_load_routine_rejects_transaction_commands(self) -> None:
        with self.assertRaises(RoutineRejected):
            self.rt.load_routine("ROSTBAD", "ROSTBAD ;\nGO ;\n TCOMMIT\n Q\n")

    def test_compile_error_raises_with_the_m_code(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            self.rt.load_routine("ROSTSYN", "ROSTSYN ;\nGO ;\n SSSS ((( \n Q\n")
        self.assertTrue(str(ctx.exception))

    def test_unbalanced_tcommit_voids_the_case(self) -> None:
        """The failure this project most needs to survive.

        A TCOMMIT in the code under test commits the verifier's own frame
        silently. load_routine() refuses such source, so this test installs it
        out of band to prove the second, runtime guard also fires.
        """
        source = "ROSTROG ;\nGO ;\n TCOMMIT\n Q\n"
        self._install_out_of_band("ROSTROG", source)
        r = self.rt.execute(ExecSpec(routine="ROSTROG", entry="GO"))
        self.assertTrue(r.void, f"unbalanced TCOMMIT was not detected: {r}")
        self.assertIn("tlevel", (r.error or "").lower())
        self.assertEqual(r.globals_out, {})

    def test_bare_trollback_voids_the_case(self) -> None:
        source = "ROSTRBK ;\nGO ;\n S ^ROSSCR(1)=1\n TROLLBACK\n Q\n"
        self._install_out_of_band("ROSTRBK", source)
        r = self.rt.execute(ExecSpec(routine="ROSTRBK", entry="GO"))
        self.assertTrue(r.void, f"bare TROLLBACK was not detected: {r}")

    def test_halt_in_the_body_kills_and_respawns_the_worker(self) -> None:
        self.rt.load_routine("ROSTHLT", "ROSTHLT ;\nGO ;\n H\n")
        spawns = self.rt.worker.spawn_count
        r = self.rt.execute(ExecSpec(routine="ROSTHLT", entry="GO"))
        self.assertTrue(r.void)
        self.assertEqual(r.error, "HALT")
        self.assertGreater(self.rt.worker.spawn_count, spawns)
        self.assertTrue(self.rt.ping(), "worker was not respawned")

    def test_timeout_kills_the_worker_and_rolls_back(self) -> None:
        self.rt.load_routine(
            "ROSTHNG", 'ROSTHNG ;\nGO ;\n S ^ROSSCR("h")=1\n F  H 1\n'
        )
        r = self.rt.execute(ExecSpec(routine="ROSTHNG", entry="GO", timeout_s=1.0))
        self.assertTrue(r.void)
        self.assertEqual(r.error, "TIMEOUT")
        self.assertTrue(self.rt.ping())
        residue = self.rt._sh(
            'source /home/vehu/etc/env && $gtm_dist/mumps -run %XCMD \'W $D(^ROSSCR)\''
        ).strip()
        self.assertEqual(residue, "0", "process death did not roll the frame back")

    def test_transaction_commands_in_callees_are_rejected(self) -> None:
        self._install_out_of_band("ROSTROG2", "ROSTROG2 ;\nGO ;\n TCOMMIT\n Q\n")
        source = "ROSTVOID ;\nGO ;\n D GO^ROSTROG2\n Q\n"
        with self.assertRaises(RoutineRejected):
            self.rt.load_routine("ROSTVOID", source)

    def test_void_cases_are_counted_and_never_scored(self) -> None:
        source = "ROSTVOID ;\nGO ;\n H\n"
        report = self.rt.verify_equivalence(
            "ROSTVOID", source, source,
            [ExecSpec(routine="ROSTVOID", entry="GO")],
        )
        self.assertEqual(report.n_void, 1)
        self.assertEqual(report.n_diverged, 0)
        self.assertFalse(report.equivalent)

    def test_callee_global_writes_are_part_of_the_verdict(self) -> None:
        self.rt.load_routine("ROSTCAL", 'ROSTCAL ;\nGO(X) ;\n S ^ROSSCR("callee")=X\n Q\n')
        baseline = "ROSTPAR ;\nGO ;\n D GO^ROSTCAL(1)\n Q\n"
        candidate = baseline.replace("ROSTCAL(1)", "ROSTCAL(2)")
        report = self.rt.verify_equivalence("ROSTPAR", baseline, candidate, [ExecSpec("ROSTPAR", "GO")])
        self.assertFalse(report.equivalent)
        self.assertEqual(report.n_void, 0)
        self.assertTrue(any(d.kind == "global" and "callee" in d.ref for d in report.divergences))

    def test_unbounded_indirect_write_cannot_certify_equivalence(self) -> None:
        baseline = "ROSTIND ;\nGO(R) ;\n S @R=1\n Q\n"
        candidate = baseline.replace("@R=1", "@R=2")
        with self.assertRaises(RoutineRejected):
            self.rt.verify_equivalence(
                "ROSTIND", baseline, candidate,
                [ExecSpec(routine="ROSTIND", entry="GO", args=['^ROSSCR("indirect")'])],
            )

    def test_truncated_global_capture_cannot_certify_equivalence(self) -> None:
        baseline = (
            "ROSTCAP ;\nGO ;\n"
            " S ^ROSSCR(1)=1,^ROSSCR(2)=2,^ROSSCR(3)=3\n Q\n"
        )
        candidate = baseline.replace("(3)=3", "(3)=4")
        with Runtime(CoreConfig(max_nodes_per_root=1)) as rt:
            report = rt.verify_equivalence(
                "ROSTCAP", baseline, candidate,
                [ExecSpec(routine="ROSTCAP", entry="GO")],
            )
            self.assertFalse(report.equivalent)
            self.assertEqual(report.n_void, 1)

    def test_candidate_compile_failure_restores_loaded_baseline(self) -> None:
        baseline = "ROSTREST ;\nGO() ;\n Q 7\n"
        with self.assertRaises(RuntimeError):
            self.rt.verify_equivalence(
                "ROSTREST", baseline, "ROSTREST ;\nGO ;\n SSSS (((\n",
                [ExecSpec(routine="ROSTREST", entry="$$GO")],
            )
        result = self.rt.execute(ExecSpec(routine="ROSTREST", entry="$$GO"))
        self.assertIsNone(result.error)
        self.assertFalse(result.void)
        self.assertEqual(result.stdout, "7")

    def test_trigger_plan_rejects_uncaptured_subtrees_and_deep_writes(self) -> None:
        with Runtime(CoreConfig(query_tier_node_cap=-1)) as rt:
            for body in (
                'M ^ROSSCR=^ROSCOPY',
                'S ^ROSSCR(1,2,3,4,5,6,7,8,9)=1',
            ):
                with self.subTest(body=body), self.assertRaises(RoutineRejected):
                    rt.load_routine("ROSDEP", "ROSDEP\nGO\n " + body + "\n Q\n")

    def test_trigger_subtree_deletion_voids_the_comparison(self) -> None:
        source = 'ROSDEL\nGO\n S ^ROSSCR(1,2)=1\n K ^ROSSCR\n Q\n'
        with Runtime(CoreConfig(query_tier_node_cap=-1)) as rt:
            report = rt.verify_equivalence("ROSDEL", source, source, [ExecSpec("ROSDEL", "GO")])
            self.assertFalse(report.equivalent)
            self.assertEqual(report.n_void, 1)

    def test_trigger_leaf_deletion_observes_final_state(self) -> None:
        source = 'ROSDEL\nGO\n S ^ROSSCR(1)=1\n K ^ROSSCR(1)\n Q\n'
        with Runtime(CoreConfig(query_tier_node_cap=-1)) as rt:
            rt.load_routine("ROSDEL", source)
            result = rt.execute(ExecSpec("ROSDEL", "GO"))
            self.assertFalse(result.void)
            self.assertEqual(result.globals_out['^ROSSCR(1)'], KILLED)

    # -- cross-process isolation ---------------------------------------------

    def test_concurrent_runtimes_do_not_read_each_others_candidates(self) -> None:
        """The worst bug this verifier can have: a confident WRONG verdict.

        Every Runtime used to share one private directory, one stdout capture
        file and one ^ROSLOG. Four runtimes verifying different candidates of
        the same routine NAME concurrently would each report whichever
        candidate happened to land last -- not an error, a wrong answer. Under
        any parallel benchmark run that silently corrupts every number.
        """
        import concurrent.futures as cf

        from rosetta.core.runtime import Runtime

        baseline = 'ROSISO ;\nGO ;\n W "base",!\n Q\n'

        def verify(tag: str) -> tuple[str, list[str]]:
            with Runtime() as rt:
                candidate = f'ROSISO ;\nGO ;\n W "{tag}",!\n Q\n'
                report = rt.verify_equivalence(
                    "ROSISO", baseline, candidate,
                    [ExecSpec(routine="ROSISO", entry="GO")],
                )
                return tag, [d.actual for d in report.divergences]

        tags = ["AAA", "BBB", "CCC", "DDD"]
        with cf.ThreadPoolExecutor(max_workers=len(tags)) as pool:
            for tag, actuals in pool.map(verify, tags):
                self.assertTrue(
                    any(tag in a for a in actuals),
                    f"runtime for {tag} saw {actuals} -- another runtime's candidate",
                )

    def test_each_runtime_gets_its_own_session(self) -> None:
        from rosetta.core.runtime import Runtime

        self.assertNotEqual(Runtime().config.session, Runtime().config.session)

    # -- isolation quality ---------------------------------------------------

    def test_no_restarts_in_the_quiesced_container(self) -> None:
        """Quiescing is what makes silent TP restarts go away. Prove it here."""
        self.rt.load_routine(
            "ROSTRST",
            'ROSTRST ;\nGO ;\n N I,V\n F I=1:1:200 S V=$G(^DPT(I,0))\n'
            ' S ^ROSSCR("r")=1\n Q\n',
        )
        for _ in range(15):
            r = self.rt.execute(ExecSpec(routine="ROSTRST", entry="GO"))
            self.assertEqual(
                r.restarts, 0,
                "TP restarted: the container is not quiesced. "
                "Run scripts/bootstrap.sh --measure.",
            )

    def test_clean_state_asserts_tlevel_zero(self) -> None:
        with self.rt.clean_state():
            pass
        self.assertTrue(self.rt.ping())

    def test_regions_are_discoverable(self) -> None:
        regions = self.rt.regions()
        self.assertTrue(regions, "no .dat regions found -- there is no snapshot path")
        names = {r for r, _ in regions}
        self.assertIn("DEFAULT", names)
        for _region, path in regions:
            self.assertTrue(path.endswith(".dat"), path)

    @unittest.skipUnless(os.environ.get("ROSETTA_SLOW_TESTS"),
                         "set ROSETTA_SLOW_TESTS=1 (full database copy/restore)")
    def test_snapshot_and_restore_round_trip(self) -> None:
        snap = self.rt.snapshot()
        self.assertTrue(snap.startswith("snap-"))
        # A read-only routine can pass even if region restoration is broken.
        # Commit an owned probe after the snapshot and prove restore removes it.
        probe = "snapshot-" + self.rt.config.session
        self.rt._sh(
            f'source {self.rt.config.env_file} && $gtm_dist/mumps -run %XCMD '
            f"'S ^ROSTMP(\"{probe}\")=1'"
        )
        self.rt.restore(snap)
        self.assertTrue(self.rt.ping())
        residue = self.rt._sh(
            f'source {self.rt.config.env_file} && $gtm_dist/mumps -run %XCMD '
            f"'W $D(^ROSTMP(\"{probe}\"))'"
        ).strip()
        self.assertEqual(residue, "0", "snapshot restoration did not restore database state")
        self.rt.load_routine("PRCHUEI", read_routine("PRCHUEI"))
        r = self.rt.execute(
            ExecSpec(routine="PRCHUEI", entry="$$VALIDUEI", args=["ZQGGH7C1MJM3"])
        )
        self.assertEqual(r.stdout, "1")

    def test_database_maintenance_refuses_an_active_worker(self) -> None:
        with self.assertRaises(WorkerError):
            self.rt._database_maintenance('printf "must-not-run"')
        self.assertTrue(self.rt.ping(), "maintenance interrupted an active worker")

    # -- helper --------------------------------------------------------------

    def _install_out_of_band(self, name: str, source: str) -> None:
        """Bypass load_routine's rejection to exercise the runtime guard."""
        from rosetta.core.analysis import parse as _parse
        from rosetta.core.runtime import LoadedRoutine

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{name}.m"
            path.write_text(source, encoding="utf-8")
            self.rt._copy_in(path, f"{self.rt.config.routine_dir}/{name}.m")
        resp = self.rt.worker.send(Request().add("CMD", "LINK").add("ROUTINE", name))
        if not resp.ok:
            raise WorkerError(resp.get("ERROR"))
        facts = _parse(name, source)
        self.rt._loaded[name] = LoadedRoutine(
            name, source, facts, plan_capture(facts)
        )


if __name__ == "__main__":
    unittest.main()
