"""The two workflows that need a verifier, expressed once.

`rosetta edit` and the TUI editor have to reach the same verdict, or the
product has two truths. This module owns those shared loops.

    for ev in edit("ORCRC", "handle a null array"):
        ...  # kind is one of: source inputs attempt verdict accepted exhausted

Every event is JSON-serialisable so TUI clients and CLI automation can consume
the same trace. Nothing here prints, and nothing here writes a file: what to do
with a verified candidate is a decision for the caller and, ultimately, a
human.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKSET = REPO_ROOT / "data" / "tasks" / "eval_tasks.json"

__all__ = ["Event", "EditTask", "WorkflowError", "cases_for", "edit", "verify"]


class WorkflowError(RuntimeError):
    """The workflow cannot produce a trustworthy verdict, so it stops."""

    def __init__(self, message: str, **data: Any) -> None:
        super().__init__(message)
        self.data: dict[str, Any] = data


@dataclass(frozen=True)
class Event:
    """One thing that happened, in the order it happened."""

    kind: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, **self.data}


@dataclass
class EditTask:
    """The three fields an agent reads. Deliberately not a DemoTask.

    A DemoTask carries recorded right/wrong rewrites for replay. There is
    nothing to replay in a real edit, and faking those fields would make a
    live run indistinguishable from a scripted one in the trace.
    """

    routine: str
    request: str
    cases: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        self.task_id = f"edit-{self.routine.lower()}"
        self.entry = self.routine
        self.title = f"edit {self.routine}"


# --------------------------------------------------------------------------
# Inputs. Verification without inputs is not verification, so this is the one
# thing every workflow needs and the thing most likely to be missing.
# --------------------------------------------------------------------------

def cases_for(
    reg: Any, routine: str, explicit: str | None = None
) -> tuple[list[dict[str, Any]] | None, str]:
    """``(cases, origin)``. ``None`` means "let the tool use its stored suite".

    Three sources, in order of authority:

      1. a file the caller named, which overrides everything
      2. the routine's stored suite, which is what an editor session would use
      3. the case lists carried by built benchmark tasks for this routine

    Source 3 exists because ``data/suites/`` is empty on a fresh checkout while
    ``data/tasks/eval_tasks.json`` already carries validated inputs for every
    routine it covers. Without it, verification would refuse on a machine that
    has everything it needs.
    """
    if explicit:
        cases = json.loads(Path(explicit).read_text(encoding="utf-8"))
        if not isinstance(cases, list) or not cases:
            raise WorkflowError(
                f"{explicit} must hold a non-empty JSON list of ExecSpecs"
            )
        return cases, explicit
    try:
        if reg is not None and routine in reg.suites.available():
            return None, str(reg.suites.path_for(routine))
    except Exception:  # a broken suite store must not hide source 3
        pass
    if not TASKSET.exists():
        return None, "no suite, no task set"
    try:
        tasks = json.loads(TASKSET.read_text(encoding="utf-8")).get("tasks", [])
    except (OSError, json.JSONDecodeError):
        return None, "no suite, unreadable task set"
    seen: set[str] = set()
    cases: list[dict[str, Any]] = []
    for t in tasks:
        if str(t.get("routine", "")).upper() != routine:
            continue
        for c in t.get("cases", []):
            key = json.dumps(c, sort_keys=True)
            if key not in seen:
                seen.add(key)
                cases.append(c)
    if not cases:
        return None, "no suite, no task covers this routine"
    return cases, f"{len(cases)} case(s) from {TASKSET.name}"


# --------------------------------------------------------------------------
# Workflow 5 — verify
# --------------------------------------------------------------------------

def verify(
    routine: str,
    candidate_src: str,
    *,
    baseline_src: str | None = None,
    cases: Sequence[dict[str, Any]] | None = None,
    registry: Any = None,
) -> dict[str, Any]:
    """Run the verifier over one candidate. Returns the rendered report.

    A verdict of "not equivalent" is a normal return, not an error. Only an
    inability to *reach* a verdict raises.
    """
    from rosetta.tools.tools import ToolError, ToolRegistry

    reg = registry or ToolRegistry()
    payload: dict[str, Any] = {
        "routine": routine.upper(),
        "candidate_src": candidate_src,
    }
    if baseline_src:
        payload["baseline_src"] = baseline_src
    if cases is not None:
        payload["cases"] = list(cases)
    try:
        return reg.call("verify_change", payload)
    except ToolError as exc:
        raise WorkflowError(str(exc), **exc.data) from exc


# --------------------------------------------------------------------------
# Workflow 1 — edit
# --------------------------------------------------------------------------

def edit(
    routine: str,
    request: str,
    *,
    model: str | None = None,
    attempts: int = 3,
    timeout_s: float = 1800.0,
    explicit_cases: str | None = None,
    registry: Any = None,
    agent: Any = None,
) -> Iterator[Event]:
    """Propose, verify, repair — until the verifier says equivalent.

    The verdict emitted is always the verifier's, never the model's claim.
    That separation is the product: a model asserting "this is behaviour
    preserving" while the verifier disagrees is the exact failure this catches.

    Yields, in order: one ``source``, one ``inputs``, then per iteration an
    ``attempt`` and a ``verdict``, and finally exactly one of ``accepted`` or
    ``exhausted``.
    """
    from rosetta.demo.agents import AgentUnavailable, RosettaAgent, unified_diff
    from rosetta.tools.tools import ToolError, ToolRegistry

    reg = registry or ToolRegistry()
    name = routine.upper()
    try:
        name, baseline, origin = reg.read_source(name)
    except ToolError as exc:
        raise WorkflowError(str(exc), **exc.data) from exc
    yield Event("source", {"routine": name, "origin": origin,
                           "lines": len(baseline.splitlines()),
                           "source": baseline})

    cases, case_origin = cases_for(reg, name, explicit_cases)
    yield Event("inputs", {"origin": case_origin,
                           "n_cases": len(cases) if cases else None})

    if agent is None:
        # 30 minutes per attempt, not the agent's own 15. A tools-on agent
        # calls `verify_change` itself and each call runs the whole case suite
        # against the real database, so the default has to allow for a model
        # that is working rather than hung. Measured: a single proposal on a
        # 134-line routine can pass ten minutes.
        agent = RosettaAgent(
            model=model, tools_on=True, cwd=str(REPO_ROOT),
            max_attempts=attempts, timeout_s=timeout_s,
        )
    task = EditTask(name, request)
    feedback: list[str] = []

    for i in range(1, attempts + 1):
        try:
            proposal = agent.propose(task, baseline, feedback)
        except AgentUnavailable as exc:
            raise WorkflowError(f"the model could not be reached: {exc}",
                                agent=getattr(agent, "name", "?"),
                                timeout_s=timeout_s) from exc
        if proposal is None:
            yield Event("exhausted", {"reason": "the model stopped proposing",
                                      "rejected": len(feedback)})
            return
        yield Event("attempt", {
            "n": i,
            "diff": proposal.diff or unified_diff(baseline, proposal.candidate_src, name),
            "explanation": getattr(proposal, "explanation", ""),
            "candidate_src": proposal.candidate_src,
        })

        yield Event("proving", {
            "n": i,
            "routine": name,
            "n_cases": len(cases) if cases else None,
            "isolation": "clean_state transaction frame per case",
            "observables": ["stdout", "runtime errors", "persistent global state"],
        })
        report = verify(name, proposal.candidate_src, baseline_src=baseline,
                        cases=cases, registry=reg)
        yield Event("verdict", {
            "n": i,
            "equivalent": bool(report["equivalent"]),
            "verdict": report["verdict"],
            "n_cases": report["n_cases"],
            "n_diverged": report["n_diverged"],
            "n_void": report["n_void"],
            "divergences": report.get("divergences", []),
            "proof_receipt": report.get("proof_receipt"),
        })
        if report["equivalent"]:
            yield Event("accepted", {
                "n": i,
                "candidate_src": proposal.candidate_src,
                "diff": proposal.diff or unified_diff(
                    baseline, proposal.candidate_src, name
                ),
            })
            return
        feedback.append(report["feedback"])

    yield Event("exhausted", {"reason": f"no verified candidate in {attempts} attempt(s)",
                              "rejected": len(feedback)})
