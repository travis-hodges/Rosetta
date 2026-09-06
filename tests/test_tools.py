"""Smoke and behaviour tests for the rosetta.tools MCP server.

No YottaDB, no container, no network. The three execution-backed tools are
exercised through the injectable seam with ``FakeBackend``; the five static
tools run against the real committed corpus and the real FileMan cache when
they are present, and are skipped with a clear reason when they are not.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from rosetta.core.interface import Divergence, ExecSpec, VerifyReport
from rosetta.tools import analysis, cases as cases_mod, fileman, report, sources
from rosetta.tools.runtime import (
    BackendUnavailable,
    FakeBackend,
    UnavailableBackend,
    backend_status,
)
from rosetta.tools.server import RosettaServer
from rosetta.tools.tools import ToolError, ToolRegistry

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS = REPO_ROOT / "data" / "routines"
DD_CACHE = REPO_ROOT / "data" / "fileman" / "dd.json.gz"

# A tiny but realistic routine: labels with formals, a naked reference, a
# postconditional, locals both NEW-ed and leaked.
SAMPLE_M = """TSTM ;test routine for rosetta.tools
LOOKUP(DFN) ;return the patient name
 N X,NAME
 S X=$G(^DPT(DFN,0))
 S NAME=$P(X,U,1)
 S:NAME="" NAME="UNKNOWN"
 S LEAKED=NAME
 Q NAME
COUNT ;count entries
 N I,N
 S (I,N)=0
 F  S I=$O(^DPT(I)) Q:I'>0  S N=N+1
 D LOG^TSTM2(N)
 Q N
"""


class RoutineNameTests(unittest.TestCase):
    def test_percent_routine_maps_to_underscore_file(self) -> None:
        self.assertEqual(sources.filename_for("%DTC"), "_DTC.m")
        self.assertEqual(sources.name_for_filename("_DTC.m"), "%DTC")

    def test_plain_routine_round_trips(self) -> None:
        self.assertEqual(sources.filename_for("xlfdt"), "XLFDT.m")
        self.assertEqual(sources.name_for_filename("XLFDT.m"), "XLFDT")

    def test_invalid_name_is_rejected_loudly(self) -> None:
        with self.assertRaises(ValueError):
            sources.normalise_name("DG-1")
        with self.assertRaises(ValueError):
            sources.normalise_name("")


@unittest.skipUnless(CORPUS.is_dir(), "data/routines corpus not present")
class RoutineStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = sources.RoutineStore(corpus_dir=CORPUS, container=None)

    def test_corpus_is_populated(self) -> None:
        self.assertGreater(len(self.store.corpus_names()), 100)

    def test_substring_pattern_matches(self) -> None:
        names, total = self.store.list("DG")
        self.assertTrue(all("DG" in n for n in names))
        self.assertEqual(total, len(names))

    def test_glob_pattern_matches(self) -> None:
        names, _ = self.store.list("DG*")
        self.assertTrue(all(n.startswith("DG") for n in names))

    def test_missing_routine_raises_with_guidance(self) -> None:
        with self.assertRaises(sources.RoutineNotFound) as ctx:
            self.store.read("NOSUCHROUTINE")
        self.assertIn("list_routines", str(ctx.exception))


class StaticAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parsed = analysis.parse_source("TSTM", SAMPLE_M)

    def test_labels_and_formals(self) -> None:
        labels = {l.label: l for l in self.parsed.labels}
        self.assertIn("LOOKUP", labels)
        self.assertEqual(labels["LOOKUP"].formals, ["DFN"])
        self.assertEqual(labels["COUNT"].formals, [])

    def test_globals_are_carets_and_split_by_direction(self) -> None:
        self.assertIn("^DPT", self.parsed.globals.read)
        self.assertEqual(self.parsed.globals.written, [])

    def test_calls_are_extracted(self) -> None:
        self.assertIn("TSTM2", self.parsed.calls)
        self.assertIn("LOG^TSTM2", self.parsed.entryrefs)

    def test_locals_separate_newed_from_leaked(self) -> None:
        loc = self.parsed.locals
        self.assertIn("DFN", loc.formals)
        self.assertIn("NAME", loc.newed)
        self.assertIn("LEAKED", loc.unscoped)
        self.assertNotIn("NAME", loc.unscoped)

    def test_locals_do_not_include_globals_or_routines(self) -> None:
        every = self.parsed.locals.all
        self.assertNotIn("DPT", every)
        self.assertNotIn("TSTM2", every)
        self.assertNotIn("LOG", every)

    def test_parse_result_is_json_serialisable(self) -> None:
        json.dumps(self.parsed.to_dict())


class CallGraphTests(unittest.TestCase):
    def test_graph_expands_and_marks_unresolved(self) -> None:
        facts = {
            "A": {"name": "A", "calls": ["B", "C"], "entryrefs": ["X^B"], "fanout": 2},
            "B": {"name": "B", "calls": ["D"], "entryrefs": [], "fanout": 1},
            "C": {"name": "C", "calls": [], "entryrefs": [], "fanout": 0},
        }
        g = analysis.build_call_graph("A", 2, facts)
        names = {n.name for n in g.nodes}
        self.assertEqual(names, {"A", "B", "C", "D"})
        self.assertEqual(g.unresolved, ["D"])
        self.assertIn("X^B", [r for e in g.edges for r in e.entryrefs])

    def test_depth_zero_returns_only_the_root(self) -> None:
        facts = {"A": {"name": "A", "calls": ["B"], "entryrefs": []}}
        g = analysis.build_call_graph("A", 0, facts)
        self.assertEqual([n.name for n in g.nodes], ["A"])

    def test_node_cap_sets_truncated(self) -> None:
        facts = {
            "A": {"name": "A", "calls": [f"R{i}" for i in range(10)], "entryrefs": []}
        }
        g = analysis.build_call_graph("A", 1, facts, node_cap=3)
        self.assertTrue(g.truncated)
        self.assertLessEqual(len(g.nodes), 3)


class FileManParsingTests(unittest.TestCase):
    def test_parse_ref_splits_subscripts(self) -> None:
        self.assertEqual(fileman.parse_ref("^DPT(3,0)"), ("^DPT", ["3", "0"]))
        self.assertEqual(fileman.parse_ref("^DPT"), ("^DPT", []))
        self.assertEqual(fileman.parse_ref("^VA(200,1,0)"), ("^VA", ["200", "1", "0"]))

    def test_parse_ref_keeps_commas_inside_quotes(self) -> None:
        _, subs = fileman.parse_ref('^XTMP("A,B",1)')
        self.assertEqual(subs, ["A,B", "1"])

    def test_parse_ref_rejects_garbage(self) -> None:
        with self.assertRaises(ValueError):
            fileman.parse_ref("!!!")

    def test_fileman_date_conversion(self) -> None:
        self.assertEqual(fileman.fileman_date("2350407"), "1935-04-07")
        self.assertEqual(
            fileman.fileman_date("3130808.083906"), "2013-08-08T08:39:06"
        )
        self.assertIsNone(fileman.fileman_date("hello"))

    def test_missing_cache_degrades_to_empty_not_crash(self) -> None:
        d = fileman.default_dictionary(Path("/nonexistent/dd.json.gz"))
        self.assertFalse(d.available)


@unittest.skipUnless(DD_CACHE.is_file(), "FileMan dd cache not built")
class FileManResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dd = fileman.DataDictionary.load(DD_CACHE)

    def test_dpt_is_file_two_patient(self) -> None:
        res = self.dd.resolve("^DPT")
        self.assertIsNotNone(res.fileman_file)
        assert res.fileman_file is not None
        self.assertEqual(res.fileman_file.number, "2")
        self.assertEqual(res.fileman_file.name, "PATIENT")

    def test_node_layout_names_the_pieces(self) -> None:
        res = self.dd.resolve("^DPT(3,0)")
        by_piece = {f.piece: f.name for f in res.node_layout}
        self.assertEqual(by_piece[1], "NAME")
        self.assertEqual(by_piece[2], "SEX")
        self.assertEqual(by_piece[3], "DATE OF BIRTH")

    def test_sample_value_is_decoded(self) -> None:
        res = self.dd.resolve("^DPT(3,0)", sample_limit=1)
        self.assertTrue(res.sample_values)
        decoded = {p.field_name: p.decoded for p in res.sample_values[0].pieces}
        self.assertEqual(decoded.get("SEX"), "MALE")
        self.assertEqual(decoded.get("DATE OF BIRTH"), "1935-04-07")

    def test_longest_root_wins_for_nested_file(self) -> None:
        res = self.dd.resolve("^VA(200,1,0)")
        self.assertTrue(res.matched_root.startswith("^VA(200"))

    def test_non_fileman_global_says_so(self) -> None:
        res = self.dd.resolve("^ZZROSETTANOTAFILE(1)")
        self.assertIsNone(res.fileman_file)
        self.assertTrue(any("not a FileMan" in n for n in res.notes))

    def test_explain_node_change_names_the_field(self) -> None:
        lines = self.dd.explain_node_change(
            "^DPT(3,0)",
            "EIGHT,PATIENT^M^2350407",
            "EIGHT,PATIENT^^2350407",
        )
        self.assertEqual(len(lines), 1)
        self.assertIn("SEX", lines[0])
        self.assertIn("piece 2", lines[0])
        self.assertIn("MALE", lines[0])

    def test_credentials_are_redacted_in_the_cache(self) -> None:
        for rows in self.dd._samples.values():  # noqa: SLF001 - asserting on the artefact
            for _ref, value in rows:
                self.assertNotRegex(value, r"!:\d:[A-Za-z0-9]{6,}")


class ReportRenderingTests(unittest.TestCase):
    def _dd(self) -> fileman.DataDictionary | None:
        return fileman.DataDictionary.load(DD_CACHE) if DD_CACHE.is_file() else None

    def test_equivalent_report_says_so(self) -> None:
        rep = VerifyReport(equivalent=True, divergences=[], n_cases=4, n_diverged=0)
        out = report.render_report(rep, None, "TSTM")
        self.assertTrue(out.equivalent)
        self.assertIn("EQUIVALENT", out.verdict)

    def test_divergence_is_specific_not_boolean(self) -> None:
        rep = VerifyReport(
            equivalent=False,
            divergences=[
                Divergence(
                    kind="global",
                    ref="^DPT(3,0)",
                    expected="EIGHT,PATIENT^M^2350407",
                    actual="EIGHT,PATIENT^^2350407",
                    case_index=2,
                )
            ],
            n_cases=5,
            n_diverged=1,
        )
        out = report.render_report(rep, self._dd(), "TSTM")
        self.assertFalse(out.equivalent)
        self.assertIn("^DPT(3,0)", out.feedback)
        self.assertIn("case 2", out.feedback)
        self.assertIn("piece 2", out.feedback)
        if DD_CACHE.is_file():
            self.assertIn("SEX", out.feedback)
            self.assertEqual(out.divergences[0].fileman_file, "#2 PATIENT")

    def test_error_divergence_explains_the_m_code(self) -> None:
        rep = VerifyReport(
            equivalent=False,
            divergences=[
                Divergence(kind="error", ref="error", expected="", actual="M7")
            ],
            n_cases=1,
            n_diverged=1,
        )
        out = report.render_report(rep, None)
        self.assertIn("undefined global", out.feedback)

    def test_void_cases_are_never_reported_as_a_pass(self) -> None:
        rep = VerifyReport(
            equivalent=True, divergences=[], n_cases=3, n_diverged=0, n_void=2
        )
        out = report.render_report(rep, None)
        self.assertIn("incomplete", out.feedback.lower())

    def test_truncation_keeps_the_true_counts(self) -> None:
        divs = [
            Divergence(kind="output", ref="stdout", expected=str(i), actual="x",
                       case_index=i)
            for i in range(10)
        ]
        rep = VerifyReport(
            equivalent=False, divergences=divs, n_cases=10, n_diverged=10
        )
        out = report.render_report(rep, None, max_divergences=3)
        self.assertEqual(len(out.divergences), 3)
        self.assertEqual(out.by_kind["output"], 10)
        self.assertIn("7 more divergence", out.feedback)


class ExecSpecValidationTests(unittest.TestCase):
    def test_valid_spec_round_trips(self) -> None:
        spec = cases_mod.spec_from_dict(
            {"routine": "tstm", "entry": "LOOKUP", "args": ["3"],
             "locals_in": {"U": "^"}}
        )
        self.assertEqual(spec.routine, "TSTM")
        self.assertEqual(spec.args, ["3"])
        self.assertEqual(cases_mod.spec_to_dict(spec)["locals_in"], {"U": "^"})

    def test_bad_types_are_rejected(self) -> None:
        for bad in (
            {"routine": "T", "args": "3"},
            {"routine": "T", "locals_in": {"U": 1}},
            {"routine": "T", "timeout_s": -1},
            {"entry": "X"},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    cases_mod.spec_from_dict(bad)

    def test_missing_task_names_the_paths_searched(self) -> None:
        store = cases_mod.TaskStore(tasks_dir=Path("/nonexistent/tasks"))
        with self.assertRaises(cases_mod.CasesUnavailable) as ctx:
            store.load("t-1")
        self.assertIn("Searched", str(ctx.exception))

    def test_missing_suite_suggests_alternatives(self) -> None:
        store = cases_mod.SuiteStore(suites_dir=Path("/nonexistent/suites"))
        with self.assertRaises(cases_mod.CasesUnavailable) as ctx:
            store.load("TSTM")
        self.assertIn("run_task_cases", str(ctx.exception))


class SeamTests(unittest.TestCase):
    def test_unavailable_backend_fails_loudly(self) -> None:
        backend = UnavailableBackend()
        with self.assertRaises(BackendUnavailable):
            backend.execute(ExecSpec(routine="TSTM"))
        with self.assertRaises(BackendUnavailable):
            backend.verify_equivalence("TSTM", "a", "b", [])

    def test_status_reports_what_core_must_export(self) -> None:
        status = backend_status()
        self.assertIn("execute", status["requires"])
        self.assertIn("verify_equivalence", status["requires"])

    def test_fake_backend_finds_the_output_divergence(self) -> None:
        backend = FakeBackend(
            outputs={("TSTM", "LOOKUP"): {"baseline": "EIGHT", "candidate": ""}}
        )
        rep = backend.verify_equivalence(
            "TSTM", "a", "b", [ExecSpec(routine="TSTM", entry="LOOKUP")]
        )
        self.assertFalse(rep.equivalent)
        self.assertEqual(rep.divergences[0].kind, "output")


def _registry(backend=None) -> ToolRegistry:
    return ToolRegistry(
        store=sources.RoutineStore(corpus_dir=CORPUS, container=None),
        backend=backend or UnavailableBackend(),
    )


class ToolBehaviourTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = _registry()

    def test_eight_tools_are_registered(self) -> None:
        names = {t.name for t in self.reg.list()}
        self.assertEqual(
            names,
            {
                "list_routines", "read_routine", "parse_routine", "call_graph",
                "resolve_global", "execute_routine", "verify_change",
                "run_task_cases",
            },
        )

    def test_every_tool_has_a_description_and_schema(self) -> None:
        for tool in self.reg.list():
            with self.subTest(tool=tool.name):
                self.assertGreater(len(tool.description), 120)
                self.assertEqual(tool.input_schema["type"], "object")
                json.dumps(tool.input_schema)

    def test_parse_routine_accepts_inline_source(self) -> None:
        out = self.reg.call("parse_routine", {"name": "TSTM", "source": SAMPLE_M})
        self.assertTrue(out["static_only"])
        self.assertIn("^DPT", out["globals"]["read"])

    def test_missing_required_parameter_is_a_tool_error(self) -> None:
        with self.assertRaises(ToolError):
            self.reg.call("read_routine", {})

    def test_execution_tools_fail_with_actionable_data(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            self.reg.call(
                "execute_routine", {"routine": "TSTM", "source": SAMPLE_M}
            )
        self.assertFalse(ctx.exception.data["executed"])
        self.assertIn("rosetta.core", str(ctx.exception))

    def test_execute_routine_reports_an_unknown_routine(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            self.reg.call("execute_routine", {"routine": "NOSUCHROUTINE"})
        self.assertIn("NOSUCHROUTINE", str(ctx.exception))

    def test_verify_change_rejects_an_identical_candidate(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            self.reg.call(
                "verify_change",
                {"routine": "TSTM", "baseline_src": SAMPLE_M,
                 "candidate_src": SAMPLE_M},
            )
        self.assertIn("identical", str(ctx.exception))

    def test_verify_change_warns_about_transaction_commands(self) -> None:
        reg = _registry(
            FakeBackend(outputs={("TSTM", None): {"baseline": "1", "candidate": "1"}})
        )
        out = reg.call(
            "verify_change",
            {
                "routine": "TSTM",
                "baseline_src": SAMPLE_M,
                "candidate_src": SAMPLE_M.replace(" Q NAME", " TCOMMIT\n Q NAME"),
                "cases": [{"routine": "TSTM"}],
            },
        )
        self.assertTrue(any("TCOMMIT" in w for w in out["static"]["warnings"]))

    def test_verify_change_reports_the_specific_global_that_moved(self) -> None:
        backend = FakeBackend(
            outputs={("TSTM", "LOOKUP"): {"baseline": "EIGHT", "candidate": "EIGHT"}},
            globals_by_version={
                "baseline": {"^DPT(3,0)": "EIGHT,PATIENT^M^2350407"},
                "candidate": {"^DPT(3,0)": "EIGHT,PATIENT^^2350407"},
            },
        )
        reg = _registry(backend)
        out = reg.call(
            "verify_change",
            {
                "routine": "TSTM",
                "baseline_src": SAMPLE_M,
                "candidate_src": SAMPLE_M.replace('U,1)', 'U,2)'),
                "cases": [{"routine": "TSTM", "entry": "LOOKUP", "args": ["3"]}],
            },
        )
        self.assertFalse(out["equivalent"])
        self.assertTrue(out["executed"])
        self.assertEqual(out["refs_touched"], ["^DPT(3,0)"])
        self.assertIn("^DPT(3,0)", out["feedback"])
        self.assertNotIn("false", out["verdict"].lower())
        receipt = out["proof_receipt"]
        self.assertEqual(receipt["schema"], "rosetta-proof/v1")
        self.assertEqual(receipt["provenance"], "FAKE BACKEND")
        self.assertFalse(receipt["live"])
        self.assertFalse(receipt["isolation"]["restored"])
        self.assertEqual(len(receipt["baseline_sha256"]), 64)
        self.assertEqual(len(receipt["candidate_sha256"]), 64)
        self.assertEqual(len(receipt["receipt_sha256"]), 64)
        self.assertNotIn("artifact", receipt)
        if DD_CACHE.is_file():
            self.assertIn("SEX", out["feedback"])

    def test_run_task_cases_scores_a_task(self) -> None:
        tasks_dir = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        (tasks_dir / "tasks.jsonl").write_text(
            json.dumps(
                {
                    "task_id": "t-001",
                    "routine": "TSTM",
                    "baseline_src": SAMPLE_M,
                    "mutated_src": SAMPLE_M.replace("U,1)", "U,2)"),
                    "operator": "BOUNDARY",
                    "line_no": 5,
                    "difficulty": "easy",
                    "cases": [{"routine": "TSTM", "entry": "LOOKUP", "args": ["3"]}],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        backend = FakeBackend(
            outputs={("TSTM", "LOOKUP"): {"baseline": "EIGHT", "candidate": "EIGHT"}}
        )
        reg = ToolRegistry(
            store=sources.RoutineStore(corpus_dir=CORPUS, container=None),
            tasks=cases_mod.TaskStore(tasks_dir=tasks_dir),
            backend=backend,
        )
        out = reg.call(
            "run_task_cases", {"task_id": "t-001", "candidate_src": SAMPLE_M}
        )
        self.assertTrue(out["solved"])
        self.assertEqual(out["operator"], "BOUNDARY")

    def test_unknown_task_id_lists_known_ids(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            self.reg.call(
                "run_task_cases", {"task_id": "nope", "candidate_src": "x"}
            )
        self.assertIn("known_task_ids", ctx.exception.data)


class JsonRpcTests(unittest.TestCase):
    def setUp(self) -> None:
        import io

        self.server = RosettaServer(registry=_registry(), log=io.StringIO())

    def _call(self, msg: dict) -> dict | None:
        out = self.server.handle_line(json.dumps(msg))
        return json.loads(out) if out is not None else None

    def test_initialize_negotiates_and_reports_the_seam(self) -> None:
        res = self._call(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {}}}
        )
        assert res is not None
        self.assertEqual(res["result"]["protocolVersion"], "2024-11-05")
        self.assertIn("tools", res["result"]["capabilities"])
        self.assertIn("backend", res["result"]["_rosetta"])

    def test_unknown_protocol_version_falls_back_to_ours(self) -> None:
        res = self._call(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "1999-01-01"}}
        )
        assert res is not None
        self.assertEqual(res["result"]["protocolVersion"], "2025-06-18")

    def test_notification_produces_no_response(self) -> None:
        self.assertIsNone(
            self._call({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )

    def test_tools_list_shape(self) -> None:
        res = self._call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert res is not None
        tools = res["result"]["tools"]
        self.assertEqual(len(tools), 8)
        for t in tools:
            self.assertIn("inputSchema", t)
            self.assertIn("description", t)

    def test_successful_tool_call_has_content_and_structured(self) -> None:
        res = self._call(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "parse_routine",
                        "arguments": {"name": "TSTM", "source": SAMPLE_M}}}
        )
        assert res is not None
        result = res["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertIn("labels", result["structuredContent"])

    def test_tool_failure_is_a_result_not_a_transport_error(self) -> None:
        res = self._call(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "read_routine",
                        "arguments": {"name": "NOSUCHROUTINE"}}}
        )
        assert res is not None
        self.assertNotIn("error", res)
        self.assertTrue(res["result"]["isError"])

    def test_unknown_tool_is_invalid_params(self) -> None:
        res = self._call(
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "nope"}}
        )
        assert res is not None
        self.assertEqual(res["error"]["code"], -32602)
        self.assertIn("available", res["error"]["data"])

    def test_unknown_method_is_method_not_found(self) -> None:
        res = self._call({"jsonrpc": "2.0", "id": 6, "method": "tools/kaboom"})
        assert res is not None
        self.assertEqual(res["error"]["code"], -32601)

    def test_malformed_json_is_parse_error_with_null_id(self) -> None:
        out = self.server.handle_line("{not json")
        assert out is not None
        res = json.loads(out)
        self.assertEqual(res["error"]["code"], -32700)
        self.assertIsNone(res["id"])

    def test_wrong_jsonrpc_version_is_invalid_request(self) -> None:
        res = self._call({"jsonrpc": "1.0", "id": 7, "method": "ping"})
        assert res is not None
        self.assertEqual(res["error"]["code"], -32600)

    def test_positional_params_are_rejected(self) -> None:
        res = self._call({"jsonrpc": "2.0", "id": 8, "method": "ping", "params": [1]})
        assert res is not None
        self.assertEqual(res["error"]["code"], -32602)

    def test_batch_returns_one_array(self) -> None:
        out = self.server.handle_line(
            json.dumps(
                [
                    {"jsonrpc": "2.0", "id": 9, "method": "ping"},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 10, "method": "tools/list"},
                ]
            )
        )
        assert out is not None
        res = json.loads(out)
        self.assertEqual([r["id"] for r in res], [9, 10])

    def test_blank_line_is_ignored(self) -> None:
        self.assertIsNone(self.server.handle_line("   "))

    def test_serve_loop_over_a_pipe(self) -> None:
        import io

        stdin = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n"
        )
        stdout = io.StringIO()
        rc = self.server.serve(stdin=stdin, stdout=stdout, )
        self.assertEqual(rc, 0)
        lines = [json.loads(l) for l in stdout.getvalue().splitlines()]
        self.assertEqual([l["id"] for l in lines], [1, 2])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
