"""Tests for the demo harness. No container, no network, no model.

Everything here runs against the committed corpus and a recorded trace, which
is the same guarantee the canned demo path makes: if these pass on a cold
laptop with the wifi off, the money moment renders on a cold laptop with the
wifi off.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rosetta.core.interface import Divergence
from rosetta.demo import agents, repair_loop, sidebyside, tasks, verify

ROOT = tasks.repo_root()


class TaskTests(unittest.TestCase):
    def test_money_moment_anchors_still_match_the_corpus(self) -> None:
        """If the corpus copy of AJETIU2 drifts, the demo must fail loudly."""
        task = tasks.NOK_TASK
        baseline = task.baseline_source()
        self.assertIn(task.wrong_edit[0], baseline)
        self.assertIn(task.right_edit[0], baseline)
        self.assertNotEqual(task.wrong_candidate(baseline), baseline)
        self.assertNotEqual(task.right_candidate(baseline), baseline)
        self.assertNotEqual(task.wrong_candidate(baseline), task.right_candidate(baseline))

    def test_candidate_changes_exactly_one_line(self) -> None:
        task = tasks.NOK_TASK
        baseline = task.baseline_source().splitlines()
        for candidate in (task.wrong_candidate(), task.right_candidate()):
            changed = [
                i for i, (a, b) in enumerate(zip(baseline, candidate.splitlines()))
                if a != b
            ]
            self.assertEqual(len(changed), 1, f"expected a one-line edit, got {changed}")

    def test_cases_use_value_args_not_m_expressions(self) -> None:
        """rosetta.core passes args as values; a quoted literal would be wrong."""
        for case in tasks.NOK_TASK.cases:
            for arg in case.spec.args:
                self.assertFalse(arg.startswith('"'), f"quoted arg leaked: {arg!r}")
            self.assertEqual(case.spec.locals_in.get("U"), "^")

    def test_missing_anchor_raises(self) -> None:
        task = tasks.NOK_TASK
        with self.assertRaises(ValueError):
            task.wrong_candidate("SOMETHING ELSE ENTIRELY\n")

    def test_unknown_task_names_the_known_ones(self) -> None:
        with self.assertRaises(KeyError) as ctx:
            tasks.get_task("nope")
        self.assertIn("nok-ajetiu2", str(ctx.exception))


class ScriptedAgentTests(unittest.TestCase):
    def test_first_attempt_is_wrong_and_only_repairs_with_feedback(self) -> None:
        agent = agents.ScriptedAgent()
        task = tasks.NOK_TASK
        baseline = task.baseline_source()

        first = agent.propose(task, baseline, [])
        assert first is not None
        self.assertEqual(first.n, 1)
        self.assertEqual(first.candidate_src, task.wrong_candidate(baseline))

        second = agent.propose(task, baseline, ["not equivalent"])
        assert second is not None
        self.assertEqual(second.n, 2)
        self.assertEqual(second.candidate_src, task.right_candidate(baseline))

        self.assertIsNone(agent.propose(task, baseline, ["a", "b"]))

    def test_backend_is_labelled_scripted(self) -> None:
        """Traces must never let a scripted run read as a model run."""
        self.assertEqual(agents.ScriptedAgent().backend, "scripted")


class TraceTests(unittest.TestCase):
    def test_records_are_flushed_line_by_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            trace = repair_loop.Trace(path, "run-1")
            trace.write("a", x=1)
            # readable before close: a partial trace is still evidence
            self.assertEqual(len(path.read_text().splitlines()), 1)
            trace.write("b", y=2)
            trace.close()
            rows = [json.loads(l) for l in path.read_text().splitlines()]
            self.assertEqual([r["kind"] for r in rows], ["a", "b"])
            self.assertEqual([r["seq"] for r in rows], [0, 1])
            self.assertTrue(all(r["run_id"] == "run-1" for r in rows))


class ConditionResultTests(unittest.TestCase):
    def test_false_confidence_needs_both_halves(self) -> None:
        make = lambda claimed, verified: repair_loop.ConditionResult(  # noqa: E731
            condition="tools_off",
            agent="x",
            backend="scripted",
            agent_claimed_success=claimed,
            verified_equivalent=verified,
        )
        self.assertTrue(make(True, False).false_confidence)
        self.assertFalse(make(True, True).false_confidence)
        self.assertFalse(make(False, False).false_confidence)
        self.assertFalse(make(True, None).false_confidence)

    def test_mcp_payload_becomes_a_case_verdict(self) -> None:
        payload = {
            "equivalent": False,
            "n_void": 0,
            "divergences": [
                {
                    "kind": "output",
                    "ref": "stdout",
                    "expected": "Not Entered",
                    "actual": "",
                    "case_index": 0,
                }
            ],
        }
        cv = repair_loop._case_verdict_from_mcp("DFN 4", "why", payload, 12)
        self.assertFalse(cv.equivalent)
        self.assertEqual(cv.divergences[0].ref, "stdout")
        self.assertEqual(cv.duration_ms, 12)


class VerifierAdapterTests(unittest.TestCase):
    def test_task_verdict_aggregates(self) -> None:
        div = Divergence(kind="output", ref="stdout", expected="a", actual="b")
        verdict = verify.TaskVerdict(
            task_id="t",
            routine="R",
            cases=[
                verify.CaseVerdict("c1", "", True),
                verify.CaseVerdict("c2", "", False, [div]),
            ],
            duration_ms=1,
        )
        self.assertFalse(verdict.equivalent)
        self.assertEqual(verdict.n_cases, 2)
        self.assertEqual(verdict.n_diverged, 1)
        self.assertIn("NOT EQUIVALENT", verdict.headline())
        self.assertEqual(verdict.to_dict()["cases"][1]["divergences"][0]["ref"], "stdout")

    def test_empty_verdict_is_not_equivalent(self) -> None:
        verdict = verify.TaskVerdict(task_id="t", routine="R", cases=[], duration_ms=0)
        self.assertFalse(verdict.equivalent)
        self.assertIn("NO VERDICT", verdict.headline())

    def test_chunking_honours_cases_per_worker(self) -> None:
        specs = list(range(5))
        self.assertEqual(len(verify._chunks(specs, 1)), 5)
        self.assertEqual(verify._chunks(specs, 0), [specs])
        self.assertEqual(len(verify._chunks(specs, 2)), 3)


class McpTapTests(unittest.TestCase):
    def test_tap_is_transparent_and_records_both_directions(self) -> None:
        echo = (
            "import sys\n"
            "for line in sys.stdin:\n"
            "    sys.stdout.write(line)\n"
            "    sys.stdout.flush()\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "tap.jsonl"
            proc = subprocess.run(
                [
                    sys.executable, "-m", "rosetta.demo.mcp_tap",
                    "--log", str(log), "--", sys.executable, "-c", echo,
                ],
                input='{"jsonrpc":"2.0","id":1,"method":"ping"}\n',
                capture_output=True,
                text=True,
                cwd=str(ROOT),
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('"method": "ping"', proc.stdout.replace('"method":"ping"', '"method": "ping"'))
            rows = [json.loads(l) for l in log.read_text().splitlines()]
            dirs = [r["dir"] for r in rows]
            self.assertIn("host->server", dirs)
            self.assertIn("server->host", dirs)

    def test_non_json_line_is_still_forwarded(self) -> None:
        echo = "import sys\nsys.stdout.write(sys.stdin.readline())\n"
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "tap.jsonl"
            proc = subprocess.run(
                [
                    sys.executable, "-m", "rosetta.demo.mcp_tap",
                    "--log", str(log), "--", sys.executable, "-c", echo,
                ],
                input="not json at all\n",
                capture_output=True,
                text=True,
                cwd=str(ROOT),
                timeout=60,
            )
            self.assertEqual(proc.stdout.strip(), "not json at all")
            self.assertTrue(
                any("raw" in json.loads(l) for l in log.read_text().splitlines())
            )


CANNED = ROOT / sidebyside.CANNED_DIR / "nok-ajetiu2.jsonl"


@unittest.skipUnless(CANNED.is_file(), f"no canned recording at {CANNED}")
class CannedSideBySideTests(unittest.TestCase):
    """The definition of done for stream G, minus the human in the room."""

    def setUp(self) -> None:
        self.rec = sidebyside.load_trace(CANNED)

    def test_recording_parses_cleanly(self) -> None:
        self.assertEqual(self.rec.problems, [])
        self.assertTrue(self.rec.usable)
        self.assertEqual(self.rec.routine, "AJETIU2")

    def test_tools_off_is_confident_and_wrong(self) -> None:
        off = self.rec.conditions["tools_off"]
        self.assertTrue(off.claimed_success)
        self.assertIs(off.verified_equivalent, False)
        self.assertEqual(off.tool_calls, 0)

    def test_tools_on_catches_it_then_repairs_to_green(self) -> None:
        on = self.rec.conditions["tools_on"]
        self.assertGreaterEqual(len(on.attempts), 2)
        self.assertIs(on.attempts[0].verdict["equivalent"], False)
        self.assertTrue(on.attempts[-1].verdict["equivalent"])
        self.assertIs(on.verified_equivalent, True)
        self.assertGreater(on.tool_calls, 0)

    def test_the_divergence_names_the_specific_value(self) -> None:
        on = self.rec.conditions["tools_on"]
        diverged = [
            c for c in on.attempts[0].verdict["cases"] if not c["equivalent"]
        ]
        self.assertTrue(diverged, "the recording must contain a real divergence")
        div = diverged[0]["divergences"][0]
        self.assertEqual(div["expected"], "Not Entered")
        self.assertEqual(div["actual"], "")

    def test_render_paints_both_sides(self) -> None:
        buf = io.StringIO()
        code = sidebyside.render(self.rec, stream=buf, color=False, width=120)
        out = buf.getvalue()
        self.assertEqual(code, 0)
        for needle in (
            "TOOLS OFF",
            "TOOLS ON",
            "NOT EQUIVALENT",
            "EQUIVALENT",
            "^DPT",
            "Not Entered",
            "AJETIU2",
        ):
            self.assertIn(needle, out, f"missing {needle!r} from the money moment")

    def test_render_survives_a_narrow_terminal(self) -> None:
        buf = io.StringIO()
        self.assertEqual(
            sidebyside.render(self.rec, stream=buf, color=False, width=40), 0
        )
        self.assertIn("NOT EQUIVALENT", buf.getvalue())

    def test_recording_is_showable(self) -> None:
        self.assertTrue(self.rec.has_money_moment)

    def test_render_is_deterministic(self) -> None:
        first, second = io.StringIO(), io.StringIO()
        sidebyside.render(self.rec, stream=first, color=False, width=120)
        sidebyside.render(
            sidebyside.load_trace(CANNED), stream=second, color=False, width=120
        )
        self.assertEqual(first.getvalue(), second.getvalue())


class DegradationTests(unittest.TestCase):
    """In front of an audience, nothing may raise."""

    def test_missing_trace_reports_instead_of_raising(self) -> None:
        rec = sidebyside.load_trace(Path("/nonexistent/nope.jsonl"))
        self.assertFalse(rec.usable)
        buf = io.StringIO()
        self.assertEqual(sidebyside.render(rec, stream=buf, color=False, width=100), 1)
        self.assertIn("unavailable", buf.getvalue())

    def test_corrupt_trace_reports_instead_of_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.jsonl"
            path.write_text('{"kind": "run_start"}\nnot json\n{]\n')
            rec = sidebyside.load_trace(path)
            self.assertTrue(rec.problems)
            buf = io.StringIO()
            self.assertEqual(
                sidebyside.render(rec, stream=buf, color=False, width=100), 1
            )

    def test_verdictless_live_trace_is_not_showable(self) -> None:
        """A run against a dead container must not become the demo."""
        rows = [
            {"kind": "run_start", "task_id": "t", "routine": "R", "title": "T"},
            {"kind": "condition_start", "condition": "tools_off", "backend": "scripted"},
            {"kind": "candidate", "condition": "tools_off", "attempt": 1, "diff": ""},
            {
                "kind": "condition_end",
                "condition": "tools_off",
                "agent_claimed_success": True,
                "verified_equivalent": None,
                "error": "verifier unavailable: docker not found",
            },
            {"kind": "condition_start", "condition": "tools_on", "backend": "scripted"},
            {"kind": "condition_end", "condition": "tools_on", "verified_equivalent": None},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.jsonl"
            path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            rec = sidebyside.load_trace(path)
            self.assertTrue(rec.usable)
            self.assertFalse(rec.has_money_moment)
            self.assertIn("docker", sidebyside._why_not(rec))

    def test_truncated_trace_still_renders_what_it_has(self) -> None:
        rows = CANNED.read_text().splitlines() if CANNED.is_file() else []
        if not rows:
            self.skipTest("no canned recording")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cut.jsonl"
            path.write_text("\n".join(rows[:-3]) + "\n")
            buf = io.StringIO()
            sidebyside.render(
                sidebyside.load_trace(path), stream=buf, color=False, width=120
            )
            self.assertIn("TOOLS OFF", buf.getvalue())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
