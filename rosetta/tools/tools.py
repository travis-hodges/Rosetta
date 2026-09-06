"""The eight analysis and evaluation tools inside the Rosetta editor.

Every ``description`` below is written for a reader who has never seen MUMPS.
That is not politeness: the description *is* the prompt. A model that does not
know what a "global" is will not call ``resolve_global`` while preparing a
change, and the tools-on arm of the benchmark measures exactly that.

Five tools are static and work with no runtime at all:
``list_routines``, ``read_routine``, ``parse_routine``, ``call_graph``,
``resolve_global``.

Three need to run code and go through the seam in :mod:`rosetta.tools.runtime`:
``execute_routine``, ``verify_change``, ``run_task_cases``. Until
``rosetta.core`` lands they return a structured, honest failure rather than a
fabricated result.
"""

from __future__ import annotations

import difflib
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from rosetta.core.interface import ExecSpec
from rosetta import proof as proof_mod

from . import analysis, cases as cases_mod, report as report_mod
from .fileman import DataDictionary, default_dictionary, parse_ref
from .runtime import BackendUnavailable, ExecutionBackend, get_backend
from .sources import RoutineNotFound, RoutineStore, default_store

__all__ = ["ToolError", "Tool", "ToolRegistry", "build_registry"]

#: Command-position transaction verbs. The frozen contract requires
#: ``load_routine`` to reject these; catching them here gives the agent a fast,
#: specific error instead of a runtime one.
_TP_RE = re.compile(
    r"(?:^|\s)(TS(?:TART)?|TC(?:OMMIT)?|TRO(?:LLBACK)?)(?:\s|:|$)",
    re.IGNORECASE | re.MULTILINE,
)


class ToolError(Exception):
    """A tool failed in a way the calling agent should read and act on.

    Surfaces as an MCP tool result with ``isError: true`` — a well-formed
    response, never a transport-level crash.
    """

    def __init__(self, message: str, **data: Any) -> None:
        super().__init__(message)
        self.data: dict[str, Any] = data


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    requires_execution: bool = False


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _require_str(args: dict[str, Any], key: str, *, upper: bool = False) -> str:
    val = args.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ToolError(
            f"parameter {key!r} is required and must be a non-empty string",
            parameter=key,
            received=type(val).__name__,
        )
    return val.strip().upper() if upper else val


def _int_arg(args: dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    val = args.get(key, default)
    if val is None:
        return default
    if isinstance(val, bool) or not isinstance(val, int):
        try:
            val = int(val)
        except (TypeError, ValueError):
            raise ToolError(f"parameter {key!r} must be an integer", parameter=key)
    return max(lo, min(hi, val))


def _tp_warnings(source: str) -> list[str]:
    hits = sorted({m.group(1).upper() for m in _TP_RE.finditer(source)})
    if not hits:
        return []
    return [
        f"Candidate source contains transaction command(s) {', '.join(hits)}. "
        "Real VistA never uses TSTART/TCOMMIT/TROLLBACK (zero occurrences across "
        "39,612 routines), and the verifier isolates every run inside its own "
        "transaction frame. Emitting one will be rejected by load_routine. "
        "Remove it."
    ]


def _static_delta(routine: str, baseline_src: str, candidate_src: str) -> dict[str, Any]:
    """What changed, statically. Free, and useful even with no runtime."""
    base = analysis.parse_source(routine, baseline_src)
    cand = analysis.parse_source(routine, candidate_src)
    diff = list(
        difflib.unified_diff(
            baseline_src.splitlines(),
            candidate_src.splitlines(),
            fromfile=f"{routine} (baseline)",
            tofile=f"{routine} (candidate)",
            lineterm="",
            n=1,
        )
    )
    changed_lines = [
        i + 1
        for i, (a, b) in enumerate(
            zip(baseline_src.splitlines(), candidate_src.splitlines())
        )
        if a != b
    ]

    def delta(a: list[str], b: list[str]) -> dict[str, list[str]]:
        return {"added": sorted(set(b) - set(a)), "removed": sorted(set(a) - set(b))}

    return {
        "identical": baseline_src == candidate_src,
        "changed_line_numbers": changed_lines[:50],
        "line_count_delta": len(candidate_src.splitlines())
        - len(baseline_src.splitlines()),
        "unified_diff": "\n".join(diff[:200]),
        "labels": delta([l.label for l in base.labels], [l.label for l in cand.labels]),
        "calls": delta(base.calls, cand.calls),
        "globals_read": delta(base.globals.read, cand.globals.read),
        "globals_written": delta(base.globals.written, cand.globals.written),
        "naked_reference_delta": (cand.globals.naked_read + cand.globals.naked_write)
        - (base.globals.naked_read + base.globals.naked_write),
        "warnings": _tp_warnings(candidate_src),
    }


def _backend_error(exc: BackendUnavailable, tool: str, **extra: Any) -> ToolError:
    return ToolError(
        str(exc),
        tool=tool,
        executed=False,
        verified=False,
        remedy=(
            "This tool needs the rosetta.core verifier. The static tools "
            "(list_routines, read_routine, parse_routine, call_graph, "
            "resolve_global) work without it."
        ),
        **extra,
    )


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


class ToolRegistry:
    """Holds the editor's analysis/evaluation tools and their collaborators.

    Every dependency is injectable so the whole surface is testable without a
    container, a database, or a network.
    """

    def __init__(
        self,
        store: RoutineStore | None = None,
        dictionary: DataDictionary | None = None,
        suites: cases_mod.SuiteStore | None = None,
        tasks: cases_mod.TaskStore | None = None,
        backend: ExecutionBackend | None = None,
    ) -> None:
        self.store = store or default_store()
        self.dictionary = dictionary if dictionary is not None else default_dictionary()
        self.suites = suites or cases_mod.default_suite_store()
        self.tasks = tasks or cases_mod.default_task_store()
        self._backend = backend
        self._tools: dict[str, Tool] = {}
        for tool in build_tools(self):
            self._tools[tool.name] = tool

    @property
    def backend(self) -> ExecutionBackend:
        return self._backend if self._backend is not None else get_backend()

    def facts(self) -> dict[str, dict[str, Any]]:
        return analysis.corpus_facts(str(self.store.corpus_dir))

    # -- lookup ------------------------------------------------------------

    def list(self) -> list[Tool]:
        return [self._tools[k] for k in sorted(self._tools)]

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(name) from None

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.get(name).handler(arguments or {})

    def read_source(self, routine: str) -> tuple[str, str, str]:
        """``(name, source, origin)`` or a :class:`ToolError` naming the problem."""
        try:
            src = self.store.read(routine)
        except ValueError as exc:
            raise ToolError(str(exc), routine=routine) from exc
        except RoutineNotFound as exc:
            names, _ = self.store.list(routine[:3] if len(routine) >= 3 else routine)
            raise ToolError(
                str(exc), routine=routine, did_you_mean=names[:10]
            ) from exc
        return src.name, src.source, src.origin


# --------------------------------------------------------------------------
# Tool bodies
# --------------------------------------------------------------------------


def build_tools(reg: ToolRegistry) -> list[Tool]:
    """Construct the eight tools bound to ``reg``."""

    # ---------------------------------------------------------------- 1 ---
    def list_routines(args: dict[str, Any]) -> dict[str, Any]:
        pattern = args.get("pattern")
        if pattern is not None and not isinstance(pattern, str):
            raise ToolError("parameter 'pattern' must be a string", parameter="pattern")
        include_container = bool(args.get("include_container", False))
        limit = _int_arg(args, "limit", 200, 1, 2000)
        names, total = reg.store.list(pattern, include_container, limit)
        return {
            "names": names,
            "returned": len(names),
            "total_matching": total,
            "truncated": total > len(names),
            "corpus_size": len(reg.store.corpus_names()),
            "searched": "benchmark corpus"
            + (" + container" if include_container else ""),
        }

    # ---------------------------------------------------------------- 2 ---
    def read_routine(args: dict[str, Any]) -> dict[str, Any]:
        name = _require_str(args, "name", upper=True)
        rname, source, origin = reg.read_source(name)
        lines = source.splitlines()
        start = _int_arg(args, "start_line", 1, 1, max(1, len(lines)))
        count = _int_arg(args, "max_lines", len(lines), 1, 20000)
        window = lines[start - 1: start - 1 + count]
        return {
            "name": rname,
            "origin": origin,
            "source": "\n".join(window),
            "start_line": start,
            "returned_lines": len(window),
            "total_lines": len(lines),
            "truncated": start - 1 + len(window) < len(lines),
        }

    # ---------------------------------------------------------------- 3 ---
    def parse_routine(args: dict[str, Any]) -> dict[str, Any]:
        name = _require_str(args, "name", upper=True)
        override = args.get("source")
        if override is not None and not isinstance(override, str):
            raise ToolError("parameter 'source' must be a string", parameter="source")
        if override:
            rname, source, origin = name, override, "caller-supplied"
        else:
            rname, source, origin = reg.read_source(name)
        parsed = analysis.parse_source(rname, source)
        out = parsed.to_dict()
        out["origin"] = origin
        out["static_only"] = True
        return out

    # ---------------------------------------------------------------- 4 ---
    def call_graph(args: dict[str, Any]) -> dict[str, Any]:
        name = _require_str(args, "name", upper=True)
        depth = _int_arg(args, "depth", 2, 0, 6)
        node_cap = _int_arg(args, "max_nodes", 200, 1, 1000)
        facts = reg.facts()
        extra = None
        if name not in facts:
            rname, source, _origin = reg.read_source(name)
            extra = (rname, source)
        follow = bool(args.get("follow_container", False))
        resolver = None
        if follow:
            def resolver(n: str) -> str | None:  # noqa: F811 - narrow closure
                found = reg.store.try_read(n)
                return found.source if found else None

        graph = analysis.build_call_graph(
            name, depth, facts, extra, node_cap, resolve_missing=resolver
        )
        out = graph.to_dict()
        out["followed_container"] = follow
        out["note"] = (
            "Edges come from DO / GOTO / JOB targets, $$ extrinsic function "
            "calls and $TEXT references. 'unresolved' callees have no source "
            "available; their own dependencies are not counted. Set "
            "follow_container=true to pull those callees from the live VistA "
            "image and keep walking (slower)."
        )
        return out

    # ---------------------------------------------------------------- 5 ---
    def resolve_global(args: dict[str, Any]) -> dict[str, Any]:
        ref = _require_str(args, "ref")
        sample_limit = _int_arg(args, "sample_limit", 5, 0, 25)
        field_limit = _int_arg(args, "field_limit", 60, 0, 2000)
        if not reg.dictionary.available:
            raise ToolError(
                "the FileMan data dictionary cache is not present; rebuild it "
                "with: python -m rosetta.tools.ddcache --build",
                ref=ref,
            )
        try:
            res = reg.dictionary.resolve(ref, sample_limit=sample_limit)
        except ValueError as exc:
            raise ToolError(str(exc), ref=ref) from exc
        out = res.to_dict()
        out["schema_field_count"] = len(res.schema)
        out["schema"] = out["schema"][:field_limit]
        out["schema_truncated"] = len(res.schema) > field_limit
        out["explanation"] = _resolution_prose(res)
        return out

    # ---------------------------------------------------------------- 6 ---
    def execute_routine(args: dict[str, Any]) -> dict[str, Any]:
        spec_args = args.get("spec") if isinstance(args.get("spec"), dict) else args
        try:
            spec = cases_mod.spec_from_dict(dict(spec_args))
        except ValueError as exc:
            raise ToolError(str(exc), parameter="spec") from exc
        source = args.get("source")
        if isinstance(source, str) and source.strip():
            for w in _tp_warnings(source):
                raise ToolError(w, routine=spec.routine)
            loaded_from = "caller-supplied"
        else:
            # The verifier runs candidates in a scratch environment, so the
            # routine must be loaded before it can be entered. Default to
            # exactly the source read_routine would return, so what runs is
            # what the agent was shown.
            _n, source, loaded_from = reg.read_source(spec.routine)
        backend = reg.backend
        try:
            backend.load_routine(spec.routine, source)
            result = backend.execute(spec)
        except BackendUnavailable as exc:
            raise _backend_error(exc, "execute_routine", spec=cases_mod.spec_to_dict(spec))
        except (RuntimeError, KeyError, OSError) as exc:
            raise ToolError(
                f"{type(exc).__name__}: {exc}",
                tool="execute_routine",
                spec=cases_mod.spec_to_dict(spec),
                executed=False,
            ) from exc
        return {
            "spec": cases_mod.spec_to_dict(spec),
            "source_loaded_from": loaded_from,
            "result": report_mod.render_exec_result(result),
            "isolation": (
                "Run inside the verifier's transaction frame; all global writes "
                "were rolled back afterwards, so nothing persisted."
            ),
        }

    # ---------------------------------------------------------------- 7 ---
    def verify_change(args: dict[str, Any]) -> dict[str, Any]:
        routine = _require_str(args, "routine", upper=True)
        candidate_src = args.get("candidate_src")
        if not isinstance(candidate_src, str) or not candidate_src.strip():
            raise ToolError(
                "parameter 'candidate_src' is required: the full replacement "
                "source for the routine, not a patch or a fragment",
                parameter="candidate_src",
            )
        baseline_src = args.get("baseline_src")
        if isinstance(baseline_src, str) and baseline_src.strip():
            origin = "caller-supplied"
        else:
            _n, baseline_src, origin = reg.read_source(routine)

        static = _static_delta(routine, baseline_src, candidate_src)
        if static["identical"]:
            raise ToolError(
                "candidate_src is byte-identical to the baseline; there is "
                "nothing to verify",
                routine=routine,
                static=static,
            )

        raw_cases = args.get("cases")
        if raw_cases is not None:
            if not isinstance(raw_cases, list) or not raw_cases:
                raise ToolError(
                    "parameter 'cases' must be a non-empty list of ExecSpec "
                    "objects when provided",
                    parameter="cases",
                )
            try:
                suite = [cases_mod.spec_from_dict(c, routine) for c in raw_cases]
            except ValueError as exc:
                raise ToolError(str(exc), parameter="cases") from exc
            case_origin = "caller-supplied"
        else:
            try:
                suite = reg.suites.load(routine)
            except cases_mod.CasesUnavailable as exc:
                raise ToolError(
                    str(exc),
                    routine=routine,
                    executed=False,
                    verified=False,
                    static=static,
                    available_suites=reg.suites.available()[:20],
                ) from exc
            case_origin = str(reg.suites.path_for(routine))

        backend = reg.backend
        started = time.perf_counter()
        try:
            rep = backend.verify_equivalence(
                routine, baseline_src, candidate_src, suite
            )
        except BackendUnavailable as exc:
            raise _backend_error(
                exc, "verify_change", routine=routine, static=static,
                n_cases=len(suite),
            )
        rendered = report_mod.render_report(rep, reg.dictionary, routine)
        out = rendered.to_dict()
        out.update(
            {
                "routine": routine,
                "baseline_origin": origin,
                "cases_origin": case_origin,
                "executed": True,
                "verified": True,
                "static": static,
            }
        )
        receipt = proof_mod.build_receipt(
            routine=routine,
            baseline_src=baseline_src,
            candidate_src=candidate_src,
            result=out,
            backend=getattr(backend, "name", type(backend).__name__),
            cases_origin=case_origin,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )
        if receipt["live"]:
            try:
                receipt["artifact"] = proof_mod.persist_receipt(receipt)
            except OSError as exc:
                receipt["artifact_error"] = f"could not save proof receipt: {exc}"
        out["proof_receipt"] = receipt
        return out

    # ---------------------------------------------------------------- 8 ---
    def run_task_cases(args: dict[str, Any]) -> dict[str, Any]:
        task_id = _require_str(args, "task_id")
        candidate_src = args.get("candidate_src")
        if not isinstance(candidate_src, str) or not candidate_src.strip():
            raise ToolError(
                "parameter 'candidate_src' is required: the full repaired "
                "source for the task's routine",
                parameter="candidate_src",
            )
        try:
            task = reg.tasks.load(task_id)
        except (cases_mod.CasesUnavailable, ValueError) as exc:
            raise ToolError(
                str(exc), task_id=task_id, executed=False, verified=False,
                known_task_ids=reg.tasks.task_ids(10),
            ) from exc

        static = _static_delta(task.routine, task.baseline_src, candidate_src)
        try:
            rep = reg.backend.verify_equivalence(
                task.routine, task.baseline_src, candidate_src, task.cases
            )
        except BackendUnavailable as exc:
            raise _backend_error(
                exc, "run_task_cases", task_id=task_id, routine=task.routine,
                static=static, n_cases=len(task.cases),
            )
        rendered = report_mod.render_report(rep, reg.dictionary, task.routine)
        out = rendered.to_dict()
        out.update(
            {
                "task_id": task.task_id,
                "routine": task.routine,
                "operator": task.operator,
                "difficulty": task.difficulty,
                "executed": True,
                "verified": True,
                "solved": rendered.equivalent and rendered.n_void == 0,
                "static": static,
            }
        )
        return out

    return [
        Tool(
            name="list_routines",
            title="List MUMPS routines",
            description=(
                "List the names of MUMPS routines available to work on.\n\n"
                "MUMPS (also called M) is the language VistA, the US Department "
                "of Veterans Affairs health system, is written in. A 'routine' "
                "is one source file containing one named unit of code, e.g. "
                "XLFDT. Names are uppercase; a leading % is legal.\n\n"
                "By default this searches the 500-routine benchmark corpus. Set "
                "include_container=true to also search all 39,612 routines in "
                "the live VistA image. Use 'pattern' to filter: a shell glob "
                "(DG*, *UTL*) if it contains * ? or [, otherwise a case-"
                "insensitive substring match. Start here when you do not know "
                "what exists."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob or substring filter, e.g. 'DG*' or 'UTL'.",
                    },
                    "include_container": {
                        "type": "boolean",
                        "default": False,
                        "description": "Also list routines outside the benchmark corpus.",
                    },
                    "limit": {
                        "type": "integer", "minimum": 1, "maximum": 2000, "default": 200,
                    },
                },
                "additionalProperties": False,
            },
            handler=list_routines,
        ),
        Tool(
            name="read_routine",
            title="Read routine source",
            description=(
                "Return the full MUMPS source of one routine.\n\n"
                "Reading MUMPS for the first time: the first line is the routine "
                "name plus a ';' comment. Any line starting in column 1 is a "
                "label (an entry point); lines starting with a space are code "
                "belonging to the label above. Commands are usually abbreviated "
                "to one letter (S=SET, D=DO, I=IF, Q=QUIT, W=WRITE, F=FOR, "
                "K=KILL, N=NEW). ';' begins a comment. '^NAME(...)' is a "
                "persistent database variable; a name with no '^' is a local.\n\n"
                "Source comes from the benchmark corpus when present, otherwise "
                "from the live VistA image. Use start_line/max_lines for long "
                "routines."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Routine name, e.g. 'XLFDT'."},
                    "start_line": {"type": "integer", "minimum": 1, "default": 1},
                    "max_lines": {"type": "integer", "minimum": 1, "maximum": 20000},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            handler=read_routine,
        ),
        Tool(
            name="parse_routine",
            title="Statically analyse a routine",
            description=(
                "Statically analyse a routine and return its structure. Reads "
                "the code; runs nothing.\n\n"
                "Returns:\n"
                "- labels: entry points with their formal arguments. A label "
                "with formals can be called as an extrinsic function, "
                "$$LABEL^ROUTINE(a,b). 'blocked_by' flags labels that read a "
                "terminal, spawn jobs, or depend on wall-clock time, which makes "
                "them unusable as deterministic test entry points.\n"
                "- calls: other routines this one invokes, plus the exact "
                "entryrefs (TAG^ROUTINE).\n"
                "- globals: persistent database variables, split into read and "
                "written. 'naked_read'/'naked_write' count naked references "
                "(^(3)), which silently reuse the subscripts of the previous "
                "global reference and are a classic bug source.\n"
                "- locals: non-persistent variables, split into formals, NEW-ed, "
                "written, read, and 'unscoped' — written but never NEW-ed, i.e. "
                "leaked into the caller's symbol table.\n\n"
                "Pass 'source' to analyse a candidate you are drafting instead "
                "of what is on disk."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "source": {
                        "type": "string",
                        "description": "Analyse this source instead of reading from disk.",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            handler=parse_routine,
        ),
        Tool(
            name="call_graph",
            title="Call graph around a routine",
            description=(
                "Return the outbound call graph rooted at a routine, expanded to "
                "'depth' hops, plus the routines that call it.\n\n"
                "Use this before changing anything: direct fan-out badly "
                "understates real dependency in VistA. A routine with one callee "
                "and no database references of its own can still reach most of "
                "the patient record two hops later. 'unresolved' lists callees "
                "with no source in the corpus — their dependencies are unknown, "
                "not zero."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "depth": {
                        "type": "integer", "minimum": 0, "maximum": 6, "default": 2,
                        "description": "Hops to expand. 1 = direct callees only.",
                    },
                    "max_nodes": {
                        "type": "integer", "minimum": 1, "maximum": 1000, "default": 200,
                    },
                    "follow_container": {
                        "type": "boolean", "default": False,
                        "description": (
                            "Keep walking into callees that are outside the "
                            "benchmark corpus by reading them from the live "
                            "VistA image. Slower, but the only way to see the "
                            "true reach of a change."
                        ),
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            handler=call_graph,
        ),
        Tool(
            name="resolve_global",
            title="Explain a database global",
            description=(
                "Explain what a MUMPS global reference actually means, using "
                "VistA's own data dictionary, and show real values from the "
                "populated database.\n\n"
                "This is usually the tool you want. MUMPS has no tables and no "
                "declared schema: the database is a set of sparse persistent "
                "arrays such as ^DPT(3,0), and the value stored there is one "
                "string with fields packed into '^'-delimited pieces. Nothing in "
                "the source tells you what those pieces are.\n\n"
                "VistA layers a data dictionary called FileMan on top, and this "
                "tool reads it. Ask about '^DPT' and you learn it is FileMan "
                "file #2, PATIENT. Ask about '^DPT(3,0)' and you additionally "
                "get the layout of node 0 — piece 1 is NAME, piece 2 is SEX, "
                "piece 3 is DATE OF BIRTH — together with the real stored record "
                "decoded piece by piece, including set-of-codes values (M = "
                "MALE) and FileMan dates (2350407 = 1935-04-07).\n\n"
                "Globals with no FileMan entry are reported as such; they are "
                "typically scratch, index, or application-private storage."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "Global reference: '^DPT', '^DPT(3,0)', '^VA(200,1,0)'.",
                    },
                    "sample_limit": {
                        "type": "integer", "minimum": 0, "maximum": 25, "default": 5,
                    },
                    "field_limit": {
                        "type": "integer", "minimum": 0, "maximum": 2000, "default": 60,
                        "description": "Cap on schema fields returned (file #2 has 594).",
                    },
                },
                "required": ["ref"],
                "additionalProperties": False,
            },
            handler=resolve_global,
        ),
        Tool(
            name="execute_routine",
            title="Execute a routine (sandboxed)",
            description=(
                "Run one routine against the real VistA database and return what "
                "it did: written output, any MUMPS error code, every database "
                "node it changed, and how long it took.\n\n"
                "The run is isolated. It happens inside a transaction frame that "
                "is rolled back afterwards, so nothing you do here persists and "
                "you cannot corrupt state for later calls.\n\n"
                "'entry' is the label to start at (null = top of routine). "
                "'args' are positional arguments. 'locals_in' pre-sets local "
                "variables — VistA code very often expects them; in particular "
                "almost everything needs U set to \"^\", and patient code needs "
                "DFN set to a patient record number. 'globals_in' pre-sets "
                "database nodes for the duration of the run.\n\n"
                "Read the result's globals_out carefully: a routine can return "
                "the right value and still write the wrong thing to the database."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "routine": {"type": "string"},
                    "entry": {
                        "type": ["string", "null"],
                        "description": "Entry label; null starts at the top of the routine.",
                    },
                    "args": {"type": "array", "items": {"type": "string"}, "default": []},
                    "locals_in": {
                        "type": "object", "additionalProperties": {"type": "string"},
                        "description": 'Local variables to pre-set, e.g. {"U": "^", "DFN": "3"}.',
                    },
                    "globals_in": {
                        "type": "object", "additionalProperties": {"type": "string"},
                        "description": 'Global nodes to pre-set, e.g. {"^X(1)": "abc"}.',
                    },
                    "timeout_s": {"type": "number", "minimum": 0.1, "default": 10.0},
                    "source": {
                        "type": "string",
                        "description": "Optional replacement source to compile before running.",
                    },
                },
                "required": ["routine"],
                "additionalProperties": False,
            },
            handler=execute_routine,
            requires_execution=True,
        ),
        Tool(
            name="verify_change",
            title="Verify a candidate rewrite against the original",
            description=(
                "Decide whether your rewritten routine behaves identically to "
                "the original, by running both on real inputs against the real "
                "database and diffing everything observable.\n\n"
                "This is the tool that makes correctness checkable instead of a "
                "matter of opinion. It does not return a boolean. When the two "
                "versions differ it tells you exactly what moved: which case, "
                "which database node, which '^'-delimited piece of that node, "
                "which FileMan field that piece is, and the before/after values "
                "— for example 'piece 2 (SEX, field .02 of file #2 PATIENT): "
                "expected \"M\" (MALE), got \"\" (<empty>)'. Feed that straight "
                "back into your next attempt.\n\n"
                "Three things are compared per case: written output, the MUMPS "
                "error code (an error is a divergence, and suppressing one the "
                "original raises is also a divergence), and the state of every "
                "database global afterwards.\n\n"
                "'candidate_src' is the complete replacement source, not a "
                "patch. Inputs come from the routine's stored suite unless you "
                "pass 'cases'. Every run is rolled back."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "routine": {"type": "string"},
                    "candidate_src": {
                        "type": "string",
                        "description": "Complete replacement source for the routine.",
                    },
                    "baseline_src": {
                        "type": "string",
                        "description": "Original source. Defaults to what is on disk.",
                    },
                    "cases": {
                        "type": "array",
                        "description": "Optional inputs to run; defaults to the stored suite.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "routine": {"type": "string"},
                                "entry": {"type": ["string", "null"]},
                                "args": {"type": "array", "items": {"type": "string"}},
                                "locals_in": {
                                    "type": "object",
                                    "additionalProperties": {"type": "string"},
                                },
                                "globals_in": {
                                    "type": "object",
                                    "additionalProperties": {"type": "string"},
                                },
                                "timeout_s": {"type": "number"},
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["routine", "candidate_src"],
                "additionalProperties": False,
            },
            handler=verify_change,
            requires_execution=True,
        ),
        Tool(
            name="run_task_cases",
            title="Score a candidate against a benchmark task",
            description=(
                "Verify a candidate against a specific benchmark task by id.\n\n"
                "Each task is one routine with one deliberately introduced "
                "defect, plus the input suite that is known to expose it. This "
                "tool loads the task's original source and its cases, runs both "
                "versions, and reports whether your candidate restored the "
                "original behaviour. 'solved' is true only when every case "
                "matched and none were void.\n\n"
                "Like verify_change, failures name the specific divergence. Use "
                "verify_change when you are working on a routine freehand and "
                "this when you have a task_id."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "candidate_src": {
                        "type": "string",
                        "description": "Complete repaired source for the task's routine.",
                    },
                },
                "required": ["task_id", "candidate_src"],
                "additionalProperties": False,
            },
            handler=run_task_cases,
            requires_execution=True,
        ),
    ]


def _resolution_prose(res: Any) -> str:
    """One paragraph a model can read without walking the JSON."""
    if res.fileman_file is None:
        return (
            f"{res.global_name} is not described by FileMan in this environment. "
            "Treat it as application-private storage and read the source to "
            "learn its layout."
        )
    f = res.fileman_file
    parts = [f"{res.matched_root or res.global_name} is FileMan file #{f.number} ({f.name})."]
    if res.record_ien:
        parts.append(f"Record (internal entry number) {res.record_ien}.")
    if res.node and res.node_layout:
        layout = ", ".join(
            f"piece {fl.piece}={fl.name}" for fl in res.node_layout[:12] if fl.piece
        )
        parts.append(f"Node {res.node!r} packs: {layout}.")
    elif res.node:
        parts.append(
            f"Node {res.node!r} has no declared fields — likely an index or "
            "cross-reference node."
        )
    if res.sample_values:
        parts.append(f"{len(res.sample_values)} real sample node(s) included below.")
    return " ".join(parts)


def build_registry(**kwargs: Any) -> ToolRegistry:
    """Convenience constructor mirroring :class:`ToolRegistry`."""
    return ToolRegistry(**kwargs)
