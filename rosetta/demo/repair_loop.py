"""Two-condition repair loop with a full auditable trace.

Runs one demo task twice:

``tools_off``
    The agent sees the source and nothing else. It proposes once, explains
    itself, and stops. Rosetta still verifies that candidate afterwards, but
    the agent never sees the result -- that hidden verdict is what turns
    "the agent sounded confident" into a number (docs/PROJECT.md #9).

``tools_on``
    The agent gets Rosetta's MCP tools. Every ``verify_change`` call goes over
    real MCP stdio to ``python3 -m rosetta.tools`` -- the same protocol
    boundary OpenCode uses -- and the verifier's divergence text is fed back so
    the agent can repair.

Every frame that matters is appended to ``results/<run_id>.jsonl``: the
candidate (with a sha1 of the exact source), every tool call and its response,
every verdict, and which verdicts the agent was allowed to see. Section 9
requires published numbers to be reproducible from these traces, so nothing is
summarised away.

Known constraint, worked around here: ``rosetta.core`` rewrites a routine's
object file in place, and YottaDB then intermittently refuses to relink it
(``%YDB-E-INVOBJFILE``) -- always by about the fourth relink of a routine that
has also been executed, and sometimes on the first. Restarting the M worker
inside one Python process does not reliably clear it; a fresh OS process does.
So every verifier call here runs in its own ``python3 -m rosetta.tools``
subprocess with exactly one case. That costs ~0.7s per case and has been
reliable across every run. The fix belongs in ``rosetta/core``.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from rosetta.core.interface import Divergence

from .agents import Agent, AgentUnavailable, Attempt, OpenCodeAgent, ScriptedAgent
from .mcp_stdio_client import McpError, McpStdioClient
from .tasks import DemoTask, get_task, repo_root
from .verify import CaseVerdict, TaskVerdict, VerifierUnavailable

__all__ = [
    "ConditionResult",
    "Trace",
    "default_server_command",
    "run_condition",
    "run_task",
    "verify_via_mcp",
]

CONDITIONS = ("tools_off", "tools_on")

#: Attempts per case before a case is reported unrunnable. See verify_via_mcp.
_VERIFY_RETRIES = 4
_VERIFY_BACKOFF_S = 1.5


def default_server_command() -> list[str]:
    """How to start the Rosetta MCP server for the tools-on condition."""
    raw = os.environ.get("ROSETTA_DEMO_MCP_COMMAND")
    if raw:
        return raw.split()
    return [sys.executable or "python3", "-m", "rosetta.tools"]


class Trace:
    """Append-only JSONL trace. One record per line, flushed immediately.

    Flushing per record is deliberate: if the harness dies mid-run the trace
    up to that point must still be readable, because a partial trace is
    evidence and a lost trace is not.
    """

    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        self.count = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8")

    def write(self, kind: str, **fields: Any) -> None:
        record = {
            "run_id": self.run_id,
            "seq": self.count,
            "ts": round(time.time(), 6),
            "kind": kind,
        }
        record.update(fields)
        self._handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._handle.flush()
        self.count += 1

    def close(self) -> None:
        try:
            self._handle.close()
        except OSError:  # pragma: no cover
            pass

    def __enter__(self) -> "Trace":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


@dataclass
class ConditionResult:
    """Outcome of running one agent under one condition."""

    condition: str
    agent: str
    backend: str
    attempts: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: int = 0
    agent_claimed_success: bool = False
    verified_equivalent: bool | None = None
    verdict: TaskVerdict | None = None
    error: str | None = None
    duration_ms: int = 0

    @property
    def false_confidence(self) -> bool:
        """Agent stopped claiming success while the verifier says it is wrong."""
        return self.agent_claimed_success and self.verified_equivalent is False

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "agent": self.agent,
            "backend": self.backend,
            "attempts": self.attempts,
            "n_attempts": len(self.attempts),
            "tool_calls": self.tool_calls,
            "agent_claimed_success": self.agent_claimed_success,
            "verified_equivalent": self.verified_equivalent,
            "false_confidence": self.false_confidence,
            "verdict": self.verdict.to_dict() if self.verdict else None,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


def _case_verdict_from_mcp(
    label: str, why: str, payload: dict[str, Any], duration_ms: int
) -> CaseVerdict:
    divergences = [
        Divergence(
            kind=d.get("kind", "output"),
            ref=d.get("ref", ""),
            expected=str(d.get("expected", "")),
            actual=str(d.get("actual", "")),
            case_index=int(d.get("case_index", 0)),
        )
        for d in payload.get("divergences", [])
    ]
    return CaseVerdict(
        label=label,
        why=why,
        equivalent=bool(payload.get("equivalent")) and not divergences,
        divergences=divergences,
        n_void=int(payload.get("n_void", 0)),
        duration_ms=duration_ms,
    )


def verify_via_mcp(
    task: DemoTask,
    candidate_src: str,
    baseline_src: str,
    trace: Trace,
    *,
    attempt: int,
    condition: str = "tools_on",
    agent_visible: bool = True,
    server_command: Sequence[str] | None = None,
    cwd: str | None = None,
) -> tuple[TaskVerdict, list[str], int]:
    """Call ``verify_change`` over real MCP stdio, one case per server process.

    ``agent_visible=False`` means the caller is scoring a candidate the agent
    will never be told about. Those frames are traced under ``scoring_*`` kinds
    so that a reader -- or the side-by-side renderer -- can never mistake them
    for tool calls the agent made.

    Returns (verdict, feedback_lines, n_tool_calls). Raises
    :class:`VerifierUnavailable` if no verdict could be reached at all.
    """
    prefix = "" if agent_visible else "scoring_"
    command = list(server_command or default_server_command())
    root = cwd or str(repo_root())
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", root)

    verdicts: list[CaseVerdict] = []
    feedback: list[str] = []
    calls = 0
    started = time.monotonic()
    failures: list[str] = []

    for case in task.cases:
        arguments = {
            "routine": task.routine,
            "candidate_src": candidate_src,
            "baseline_src": baseline_src,
            "cases": [
                {
                    "routine": case.spec.routine,
                    "entry": case.spec.entry,
                    "args": list(case.spec.args),
                    "locals_in": dict(case.spec.locals_in),
                    "timeout_s": case.spec.timeout_s,
                }
            ],
        }
        trace.write(
            f"{prefix}tool_call",
            condition=condition,
            attempt=attempt,
            tool="verify_change",
            case=case.label,
            transport="mcp-stdio",
            server_command=command,
            arguments=arguments,
        )
        case_started = time.monotonic()
        result = None
        last_error = ""
        # A fresh server per case, and retries on top: the routine's object
        # file is rewritten in place in a directory shared by every verifier
        # process, so a concurrent Rosetta run (a bench build, another demo)
        # can make ZLINK fail with INVOBJFILE at any moment. Backing off and
        # re-linking clears it. Never fabricate a verdict for a case that did
        # not run -- an unrunnable case is reported, not scored.
        for retry in range(_VERIFY_RETRIES):
            calls += 1
            try:
                with McpStdioClient(
                    command, cwd=root, env=env, timeout_s=180.0
                ) as client:
                    candidate_result = client.call_tool("verify_change", arguments)
            except (McpError, OSError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                payload_probe = candidate_result.raw.get("structuredContent") or {}
                if not candidate_result.is_error and "equivalent" in payload_probe:
                    result = candidate_result
                    break
                last_error = candidate_result.text.strip()[:300]
            trace.write(
                f"{prefix}tool_retry",
                condition=condition,
                attempt=attempt,
                tool="verify_change",
                case=case.label,
                try_number=retry + 1,
                error=last_error,
            )
            time.sleep(_VERIFY_BACKOFF_S * (retry + 1))

        if result is None:
            failures.append(f"{case.label}: {last_error}")
            trace.write(
                f"{prefix}tool_error",
                condition=condition,
                attempt=attempt,
                tool="verify_change",
                case=case.label,
                error=last_error,
            )
            continue

        elapsed = int((time.monotonic() - case_started) * 1000)
        payload = result.raw.get("structuredContent") or {}
        trace.write(
            f"{prefix}tool_result",
            condition=condition,
            attempt=attempt,
            tool="verify_change",
            case=case.label,
            is_error=result.is_error,
            duration_ms=elapsed,
            text=result.text,
            structured=payload,
        )
        verdicts.append(_case_verdict_from_mcp(case.label, case.why, payload, elapsed))
        if not payload.get("equivalent"):
            note = payload.get("feedback") or result.text
            feedback.append(f"[{case.label} — {case.why}]\n{note}")

    if not verdicts:
        raise VerifierUnavailable(
            "verify_change reached no verdict on any case:\n  "
            + "\n  ".join(failures or ["no cases"])
        )

    verdict = TaskVerdict(
        task_id=task.task_id,
        routine=task.routine,
        cases=verdicts,
        duration_ms=int((time.monotonic() - started) * 1000),
        source="live",
    )
    return verdict, feedback, calls


def run_condition(
    task: DemoTask,
    agent: Agent,
    condition: str,
    trace: Trace,
    *,
    max_attempts: int = 3,
    server_command: Sequence[str] | None = None,
) -> ConditionResult:
    """Run one agent under one condition, tracing everything it does."""
    if condition not in CONDITIONS:
        raise ValueError(f"condition must be one of {CONDITIONS}, got {condition!r}")

    tools_on = condition == "tools_on"
    baseline = task.baseline_source()
    result = ConditionResult(
        condition=condition, agent=agent.name, backend=agent.backend
    )
    trace.write(
        "condition_start",
        condition=condition,
        agent=agent.name,
        backend=agent.backend,
        task_id=task.task_id,
        routine=task.routine,
        request=task.request,
        n_cases=len(task.cases),
        tools_available=["verify_change"] if tools_on else [],
    )
    started = time.monotonic()
    feedback: list[str] = []
    last: Attempt | None = None

    try:
        for _ in range(max_attempts):
            try:
                attempt = agent.propose(task, baseline, feedback)
            except AgentUnavailable as exc:
                result.error = f"agent unavailable: {exc}"
                trace.write("agent_error", condition=condition, error=result.error)
                break
            if attempt is None:
                break
            last = attempt
            result.attempts.append({**attempt.to_dict(), "final": False})
            trace.write(
                "candidate",
                condition=condition,
                backend=agent.backend,
                **attempt.to_dict(),
                candidate_src=attempt.candidate_src,
            )

            if not tools_on:
                # The agent has no verifier. It stops here and it is confident.
                result.agent_claimed_success = True
                break

            try:
                verdict, feedback_new, calls = verify_via_mcp(
                    task,
                    attempt.candidate_src,
                    baseline,
                    trace,
                    attempt=attempt.n,
                    server_command=server_command,
                )
            except VerifierUnavailable as exc:
                result.error = f"verifier unavailable: {exc}"
                trace.write("verifier_error", condition=condition, error=result.error)
                break

            result.tool_calls += calls
            result.verdict = verdict
            result.verified_equivalent = verdict.equivalent
            trace.write(
                "verdict",
                condition=condition,
                attempt=attempt.n,
                agent_visible=True,
                **verdict.to_dict(),
            )
            if verdict.equivalent:
                result.agent_claimed_success = True
                break
            feedback.append("\n\n".join(feedback_new))
    finally:
        result.duration_ms = int((time.monotonic() - started) * 1000)

    # Score the agent's final candidate whether or not it ever saw a verdict.
    #
    # This goes over MCP even in the tools-off condition. That is a transport
    # choice, not a semantic one: the agent never sees this verdict (the trace
    # marks it agent_visible=False). It is here because a fresh server process
    # per case is the only configuration that has proved reliable against the
    # relink fault -- the in-process API in rosetta.demo.verify fails a case
    # outright roughly one run in three even with retries. Scoring that dropped
    # cases at random would corrupt the only number the demo publishes.
    if last is not None and result.verified_equivalent is None:
        try:
            verdict, _, _ = verify_via_mcp(
                task,
                last.candidate_src,
                baseline,
                trace,
                attempt=last.n,
                condition=condition,
                agent_visible=False,
                server_command=server_command,
            )
        except VerifierUnavailable as exc:
            result.error = result.error or f"scoring verification failed: {exc}"
            trace.write("verifier_error", condition=condition, error=str(exc))
        else:
            result.verdict = verdict
            result.verified_equivalent = verdict.equivalent
            trace.write(
                "verdict",
                condition=condition,
                attempt=last.n,
                agent_visible=False,  # hidden: this is scoring, not feedback
                **verdict.to_dict(),
            )

    if result.attempts:
        result.attempts[-1]["final"] = True
    trace.write("condition_end", **result.to_dict())
    return result


def _make_agent(backend: str, tools_on: bool, model: str | None) -> Agent:
    if backend == "scripted":
        return ScriptedAgent()
    if backend == "opencode":
        return OpenCodeAgent(model=model, tools_on=tools_on, cwd=str(repo_root()))
    raise ValueError(f"unknown agent backend {backend!r} (scripted|opencode)")


def run_task(
    task: DemoTask,
    *,
    backend: str = "scripted",
    model: str | None = None,
    conditions: Sequence[str] = CONDITIONS,
    out_dir: Path | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run a task under each condition and write the trace. Returns a summary."""
    out_dir = out_dir or (repo_root() / "results")
    run_id = run_id or f"{task.task_id}-{backend}-{uuid.uuid4().hex[:8]}"
    trace_path = out_dir / f"{run_id}.jsonl"

    results: dict[str, ConditionResult] = {}
    with Trace(trace_path, run_id) as trace:
        trace.write(
            "run_start",
            task_id=task.task_id,
            routine=task.routine,
            title=task.title,
            request=task.request,
            backend=backend,
            model=model,
            conditions=list(conditions),
            stakes=task.stakes,
            baseline_sha1=_sha1(task.baseline_source()),
            python=platform.python_version(),
            platform=platform.platform(),
        )
        for condition in conditions:
            agent = _make_agent(backend, condition == "tools_on", model)
            results[condition] = run_condition(task, agent, condition, trace)
        summary = {
            "run_id": run_id,
            "task_id": task.task_id,
            "routine": task.routine,
            "title": task.title,
            "request": task.request,
            "stakes": task.stakes,
            "backend": backend,
            "model": model,
            "trace": str(trace_path),
            "conditions": {k: v.to_dict() for k, v in results.items()},
        }
        trace.write("run_end", summary=summary)

    summary_path = out_dir / f"{run_id}.summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    summary["summary_path"] = str(summary_path)
    return summary


def _sha1(text: str) -> str:
    import hashlib

    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m rosetta.demo.repair_loop",
        description="Run a demo task with tools off and tools on, tracing everything.",
    )
    parser.add_argument("--task", default="nok-ajetiu2")
    parser.add_argument(
        "--backend",
        default="scripted",
        choices=("scripted", "opencode"),
        help="scripted = offline replay (no model); opencode = real CLI, needs credentials",
    )
    parser.add_argument("--model", default=None, help="provider/model for --backend opencode")
    parser.add_argument("--tools", default="both", choices=("off", "on", "both"))
    parser.add_argument("--out", default=None, help="output directory (default results/)")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)

    conditions = (
        CONDITIONS
        if args.tools == "both"
        else ("tools_off",)
        if args.tools == "off"
        else ("tools_on",)
    )
    try:
        task = get_task(args.task)
    except KeyError as exc:
        print(exc, file=sys.stderr)
        return 2

    summary = run_task(
        task,
        backend=args.backend,
        model=args.model,
        conditions=conditions,
        out_dir=Path(args.out) if args.out else None,
        run_id=args.run_id,
    )

    print(f"task     {summary['task_id']}  ({summary['routine']})")
    print(f"backend  {summary['backend']}")
    for name, cond in summary["conditions"].items():
        verdict = cond["verified_equivalent"]
        state = {True: "EQUIVALENT", False: "NOT EQUIVALENT", None: "NO VERDICT"}[verdict]
        print(
            f"  {name:<10} attempts={cond['n_attempts']} "
            f"tool_calls={cond['tool_calls']} "
            f"claimed_success={cond['agent_claimed_success']} "
            f"verifier={state}"
            + ("  <-- FALSE CONFIDENCE" if cond["false_confidence"] else "")
        )
        if cond["error"]:
            print(f"             error: {cond['error']}")
    print(f"trace    {summary['trace']}")
    print(f"summary  {summary['summary_path']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
