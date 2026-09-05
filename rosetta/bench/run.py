"""Execute a model over the benchmark task set, in each condition.

Section 6 lists this as `run.py`: "execute a model over tasks with
`--tools on|off`, N attempts, capture traces". It is the bridge between the
demo harness (which drives one task for an audience) and the scorer (which
reads `results/bench/*.jsonl` and produces the published numbers).

Two conditions map onto the frozen vocabulary section 9 uses:

    baseline     stock model, file access only, no Rosetta tools
    scaffolded   same model, Rosetta tools including verify_change

The verdict written to every trace is the HARNESS's own post-hoc
`verify_equivalence` against the task's frozen case suite -- never whatever
the agent believed. That separation is the whole point: the gap between the
agent's assertion and the harness's verdict IS the false-confidence rate.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from rosetta.bench.trace import Assertion, ToolCall, TraceRecord, Verdict, write_traces
from rosetta.core.interface import ExecSpec

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TASKSET = REPO_ROOT / "data" / "tasks" / "eval_tasks.json"
DEFAULT_OUT = REPO_ROOT / "results" / "bench"

CONDITIONS = {"baseline": False, "scaffolded": True}  # name -> tools_on

REQUEST = (
    "This MUMPS routine contains an injected regression: exactly one edit was made "
    "to a working routine. Produce a corrected version whose behaviour is equivalent "
    "to the original, pre-mutation routine. Change nothing else."
)


class RunRefused(RuntimeError):
    """The run cannot produce a trustworthy trace, so it does not start."""


@dataclass
class _TaskShim:
    """Minimal stand-in for demo.tasks.DemoTask.

    OpenCodeAgent reads only `routine` and `request`; the demo's replay fields
    (wrong_edit/right_edit) are meaningless for a benchmark task and are
    deliberately absent rather than faked.
    """

    task_id: str
    routine: str
    entry: str
    request: str
    title: str = ""
    cases: tuple[()] = ()


def load_taskset(path: Path = DEFAULT_TASKSET) -> dict[str, Any]:
    if not path.exists():
        raise RunRefused(f"{path} not found. Build a task set first.")
    doc = json.loads(path.read_text())
    if not doc.get("tasks"):
        raise RunRefused(f"{path} contains no tasks.")
    return doc


def _cases(task: dict[str, Any]) -> list[ExecSpec]:
    out: list[ExecSpec] = []
    for c in task.get("cases", []):
        if isinstance(c, dict):
            out.append(ExecSpec(**{k: v for k, v in c.items() if k in ExecSpec.__annotations__}))
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_task(
    task: dict[str, Any],
    condition: str,
    agent: Any,
    verify: Any,
    run_id: str,
    model: str,
    max_attempts: int,
) -> list[TraceRecord]:
    """Drive one task in one condition. Returns one record per attempt."""
    tools_on = CONDITIONS[condition]
    task_id = task["task_id"]
    baseline_src = task["baseline_src"]
    mutated_src = task["mutated_src"]
    cases = _cases(task)
    shim = _TaskShim(
        task_id=task_id,
        routine=task["routine"],
        entry=task.get("entry") or "",
        request=f"{REQUEST}\n\nRoutine {task['routine']}:\n{mutated_src}",
    )

    records: list[TraceRecord] = []
    feedback: list[str] = []
    for attempt in range(1, max_attempts + 1):
        started = _now()
        harness_error: str | None = None
        proposal = None
        try:
            proposal = agent.propose(shim, mutated_src, feedback)
        except Exception as exc:  # noqa: BLE001 -- a dead agent is harness error
            harness_error = f"{type(exc).__name__}: {exc}"
        if proposal is None and not harness_error:
            break  # the agent declined to continue; not a failure

        candidate = getattr(proposal, "candidate_src", None) if proposal else None
        verdict = None
        tool_calls: tuple[ToolCall, ...] = ()
        if candidate and not harness_error:
            # The harness grades. Never trust the agent's own claim.
            try:
                report = verify(task["routine"], baseline_src, candidate, cases)
                verdict = Verdict.from_report(report)
                if tools_on:
                    tool_calls = (
                        ToolCall(name="verify_change", ok=True,
                                 equivalent=report.equivalent, at=_now()),
                    )
                    if not report.equivalent:
                        feedback.append(report.summary() if hasattr(report, "summary")
                                        else str(report.divergences))
            except Exception as exc:  # noqa: BLE001
                harness_error = f"verify: {type(exc).__name__}: {exc}"

        records.append(
            TraceRecord(
                condition=condition,
                task_id=task_id,
                attempt=attempt,
                run_id=run_id,
                routine=task["routine"],
                operator=task.get("operator", ""),
                difficulty=task.get("difficulty", ""),
                model=model,
                started_at=started,
                finished_at=_now(),
                submitted=bool(candidate),
                candidate_src=candidate,
                # Submitting a candidate as the answer IS a claim it is correct.
                assertion=Assertion(
                    claimed_correct=bool(candidate) or None,
                    source="submission" if candidate else "unknown",
                    evidence=(getattr(proposal, "explanation", "") or "")[:400],
                ),
                tool_calls=tool_calls,
                verdict=verdict,
                harness_error=harness_error,
            )
        )
        if verdict is not None and verdict.equivalent:
            break
        if harness_error:
            break
    return records


def run_benchmark(
    taskset: dict[str, Any],
    conditions: Sequence[str],
    backend: str,
    model: str | None,
    max_attempts: int,
    out_dir: Path,
    limit: int | None = None,
    progress: Any = None,
) -> Path:
    from rosetta.core import verify_equivalence
    from rosetta.demo.agents import OpenCodeAgent

    tasks = taskset["tasks"][:limit] if limit else taskset["tasks"]
    run_id = f"{backend}-{int(time.time())}"
    records: list[TraceRecord] = []

    for condition in conditions:
        tools_on = CONDITIONS[condition]
        if backend == "opencode":
            agent = OpenCodeAgent(model=model, tools_on=tools_on,
                                  max_attempts=max_attempts)
        else:
            # ScriptedAgent replays a demo task's two recorded rewrites. A
            # benchmark task has no recorded rewrites, so it would produce a
            # harness_error for every record -- a run that looks like it
            # happened and scores nothing. Refuse instead.
            raise RunRefused(
                "the scripted backend replays recorded demo edits and cannot "
                "answer arbitrary benchmark tasks; use --backend opencode"
            )
        for i, task in enumerate(tasks, start=1):
            if progress:
                progress(f"{condition} {i}/{len(tasks)} {task['task_id']}")
            records.extend(
                run_task(task, condition, agent, verify_equivalence,
                         run_id, model or backend, max_attempts)
            )

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_id}.jsonl"
    write_traces(records, path)
    return path


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--taskset", type=Path, default=DEFAULT_TASKSET)
    ap.add_argument("--backend", choices=["scripted", "opencode"], default="opencode")
    ap.add_argument("--model", default=None)
    ap.add_argument("--conditions", default="baseline,scaffolded")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    bad = [c for c in conditions if c not in CONDITIONS]
    if bad:
        raise RunRefused(f"unknown condition(s): {bad}. Use {sorted(CONDITIONS)}.")

    taskset = load_taskset(args.taskset)
    path = run_benchmark(
        taskset, conditions, args.backend, args.model, args.attempts, args.out,
        args.limit, None if args.quiet else lambda m: print(f"  {m}", file=sys.stderr),
    )
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
