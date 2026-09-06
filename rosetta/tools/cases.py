"""Input suites and benchmark tasks — the *cases* half of verification.

Deciding whether two versions of a routine behave the same requires inputs to
run them on. Those come from two places, both produced by other streams:

``data/tasks/suites/<ROUTINE>.json``
    A reusable input suite for one routine, used by ``verify_change`` when the
    caller does not pass cases inline::

        {"routine": "XLFDT",
         "cases": [{"routine": "XLFDT", "entry": "HTFM",
                    "args": ["61234,3600"], "locals_in": {"U": "^"},
                    "globals_in": {}, "timeout_s": 10.0}]}

``data/tasks/tasks.jsonl`` (or ``data/tasks/<task_id>.json``)
    Generated mutation tasks, used by ``run_task_cases``. One JSON object per
    line, matching ``MutationTask`` in PROJECT.md section 6::

        {"task_id": "...", "routine": "...", "baseline_src": "...",
         "mutated_src": "...", "operator": "CMP_FLIP", "line_no": 12,
         "difficulty": "easy", "cases": [ ...ExecSpec dicts... ]}

Neither file exists yet — ``rosetta.mutate`` writes them. Until then both tools
return a well-formed error naming the exact path they looked in. This module
never writes ``data/tasks/split.lock.json`` and never writes anything at all.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Iterator

from rosetta.core.interface import ExecSpec

__all__ = [
    "CasesUnavailable",
    "MutationTaskRecord",
    "spec_from_dict",
    "spec_to_dict",
    "SuiteStore",
    "TaskStore",
    "default_suite_store",
    "default_task_store",
]

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TASKS_DIR = _REPO_ROOT / "data" / "tasks"
DEFAULT_SUITES_DIR = DEFAULT_TASKS_DIR / "suites"


class CasesUnavailable(LookupError):
    """No input suite or task found. Carries the paths that were searched."""


def spec_from_dict(data: dict[str, Any], default_routine: str | None = None) -> ExecSpec:
    """Build a frozen-contract :class:`ExecSpec` from loose JSON.

    Validates types rather than trusting them: a bad case in a generated file
    must fail here, not halfway through a benchmark run.
    """
    if not isinstance(data, dict):
        raise ValueError(f"case must be an object, got {type(data).__name__}")
    routine = data.get("routine") or default_routine
    if not routine:
        raise ValueError("case is missing 'routine'")
    entry = data.get("entry")
    if entry is not None and not isinstance(entry, str):
        raise ValueError("case 'entry' must be a string or null")
    args = data.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError("case 'args' must be a list of strings")
    locals_in = data.get("locals_in", {})
    globals_in = data.get("globals_in", {})
    for label, obj in (("locals_in", locals_in), ("globals_in", globals_in)):
        if not isinstance(obj, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in obj.items()
        ):
            raise ValueError(f"case {label!r} must be an object of string->string")
    timeout = data.get("timeout_s", 10.0)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("case 'timeout_s' must be a positive number")
    return ExecSpec(
        routine=str(routine).upper(),
        entry=entry,
        args=list(args),
        locals_in=dict(locals_in),
        globals_in=dict(globals_in),
        timeout_s=float(timeout),
    )


def spec_to_dict(spec: ExecSpec) -> dict[str, Any]:
    return {
        "routine": spec.routine,
        "entry": spec.entry,
        "args": list(spec.args),
        "locals_in": dict(spec.locals_in),
        "globals_in": dict(spec.globals_in),
        "timeout_s": spec.timeout_s,
    }


@dataclass(frozen=True)
class MutationTaskRecord:
    """One benchmark task as stored on disk. Mirrors PROJECT.md section 6."""

    task_id: str
    routine: str
    baseline_src: str
    mutated_src: str
    operator: str
    line_no: int
    difficulty: str
    cases: list[ExecSpec] = dc_field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MutationTaskRecord":
        required = ("task_id", "routine", "baseline_src", "cases")
        missing = [k for k in required if k not in data]
        if missing:
            raise ValueError(f"task record is missing {', '.join(missing)}")
        routine = str(data["routine"]).upper()
        raw_cases = data["cases"]
        if not isinstance(raw_cases, list):
            raise ValueError("task 'cases' must be a list")
        return cls(
            task_id=str(data["task_id"]),
            routine=routine,
            baseline_src=str(data["baseline_src"]),
            mutated_src=str(data.get("mutated_src", "")),
            operator=str(data.get("operator", "")),
            line_no=int(data.get("line_no", 0) or 0),
            difficulty=str(data.get("difficulty", "")),
            cases=[spec_from_dict(c, routine) for c in raw_cases],
        )


class SuiteStore:
    """Reads per-routine input suites from ``data/tasks/suites/``."""

    def __init__(self, suites_dir: Path | None = None) -> None:
        task_dir = Path(os.environ.get("ROSETTA_TASKS_DIR") or DEFAULT_TASKS_DIR)
        self.suites_dir = Path(suites_dir or os.environ.get("ROSETTA_SUITES_DIR") or task_dir / "suites").expanduser()

    def path_for(self, routine: str) -> Path:
        from .sources import normalise_name
        normalise_name(routine)
        return self.suites_dir / f"{routine.upper()}.json"

    def available(self) -> list[str]:
        if not self.suites_dir.is_dir():
            return []
        return sorted(p.stem.upper() for p in self.suites_dir.glob("*.json"))

    def load(self, routine: str) -> list[ExecSpec]:
        """Return the stored suite. Raises :class:`CasesUnavailable` if absent."""
        p = self.path_for(routine)
        if not p.is_file():
            raise CasesUnavailable(
                f"no input suite for routine {routine.upper()!r}: expected {p}. "
                "Pass 'cases' inline, or use run_task_cases with a benchmark "
                "task_id. Suites are generated by rosetta.mutate."
            )
        data = json.loads(p.read_text(encoding="utf-8"))
        raw = data.get("cases") if isinstance(data, dict) else data
        if not isinstance(raw, list) or not raw:
            raise CasesUnavailable(f"input suite {p} contains no cases")
        return [spec_from_dict(c, routine.upper()) for c in raw]


class TaskStore:
    """Reads generated mutation tasks from ``data/tasks/``."""

    def __init__(self, tasks_dir: Path | None = None) -> None:
        self.tasks_dir = Path(tasks_dir or os.environ.get("ROSETTA_TASKS_DIR") or DEFAULT_TASKS_DIR).expanduser()
        self._index: dict[str, MutationTaskRecord] | None = None

    def _candidates(self) -> list[Path]:
        return [
            self.tasks_dir / "tasks.jsonl",
            self.tasks_dir / "tasks.json",
        ]

    def _iter_records(self) -> Iterator[dict[str, Any]]:
        jsonl = self.tasks_dir / "tasks.jsonl"
        if jsonl.is_file():
            for line_no, line in enumerate(
                jsonl.read_text(encoding="utf-8").splitlines(), start=1
            ):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{jsonl}:{line_no}: {exc}") from exc
        blob = self.tasks_dir / "tasks.json"
        if blob.is_file():
            data = json.loads(blob.read_text(encoding="utf-8"))
            records = data.get("tasks") if isinstance(data, dict) else data
            if isinstance(records, list):
                yield from (r for r in records if isinstance(r, dict))

    def index(self) -> dict[str, MutationTaskRecord]:
        if self._index is None:
            out: dict[str, MutationTaskRecord] = {}
            for raw in self._iter_records():
                try:
                    rec = MutationTaskRecord.from_dict(raw)
                except ValueError as exc:
                    raise ValueError(f"invalid task in {self.tasks_dir}: {exc}") from exc
                out[rec.task_id] = rec
            self._index = out
        return self._index

    def load(self, task_id: str) -> MutationTaskRecord:
        """Return one task. Raises :class:`CasesUnavailable` if not found."""
        if not task_id or Path(task_id).name != task_id or task_id in {".", ".."}:
            raise ValueError("task_id must be a file name, not a path")
        single = self.tasks_dir / f"{task_id}.json"
        if single.is_file():
            return MutationTaskRecord.from_dict(
                json.loads(single.read_text(encoding="utf-8"))
            )
        idx = self.index()
        if task_id in idx:
            return idx[task_id]
        searched = ", ".join(str(p) for p in [*self._candidates(), single])
        known = sorted(idx)[:10]
        hint = f" Known task_ids include: {', '.join(known)}." if known else (
            " No task files exist yet; rosetta.mutate has not generated them."
        )
        raise CasesUnavailable(
            f"task {task_id!r} not found. Searched: {searched}.{hint}"
        )

    def task_ids(self, limit: int = 50) -> list[str]:
        return sorted(self.index())[:limit]


_SUITES: SuiteStore | None = None
_TASKS: TaskStore | None = None


def default_suite_store() -> SuiteStore:
    global _SUITES
    if _SUITES is None:
        _SUITES = SuiteStore()
    return _SUITES


def default_task_store() -> TaskStore:
    global _TASKS
    if _TASKS is None:
        _TASKS = TaskStore()
    return _TASKS
