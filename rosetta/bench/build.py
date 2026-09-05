"""Assemble the benchmark task set from the eval half of the locked split.

This is the bench-side gate. :mod:`rosetta.mutate` manufactures candidate
mutants and validates them against the real verifier; ``build.py`` decides which
of them become *benchmark tasks*, records why the rest did not, and writes an
artifact with enough provenance to reproduce the run.

Section 9's validity rules are re-checked here rather than trusted, because the
generator and the benchmark are separate concerns and the benchmark is the thing
whose numbers get published:

1. **killable** -- the generator's ``verify_equivalence`` said
   ``equivalent=False``; we additionally require ``n_diverged >= 1``.
2. **compiles** -- a mutant that fails ``load_routine`` surfaces as a
   ``load_failed:*`` rejection from the generator and never reaches admission.
3. **specific divergence** -- at least one ``output``, ``global`` or ``error``
   divergence. A mutant whose only signal is a timeout is **not admitted**; it
   is written to a separate ``weak_tasks`` list so the weak signal is visible
   rather than silently mixed into the headline set.
4. **eval split** -- the routine is in ``data/tasks/split.lock.json``'s eval
   list. This module reads that file and never writes it.

Two further gates are ours, not section 9's:

* **validated_by** -- a task validated by the ``static-stub`` verifier has never
  been executed. It is refused for a publishable task set, because admitting it
  would mean an unkillable mutant could reach the benchmark.
* **duplicate mutants** -- two operators can land on the same mutated source.
  The second is rejected as ``duplicate_mutant``; scoring the same defect twice
  would silently weight it double.

Usage::

    python -m rosetta.bench.build --verifier core --target-tasks 200
    python -m rosetta.bench.build --dry-run          # what would be attempted
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from rosetta.bench import split as split_mod

__all__ = [
    "SCHEMA",
    "SPECIFIC_KINDS",
    "STUB_VALIDATORS",
    "Admission",
    "BuildConfig",
    "TaskSet",
    "TaskSetRefused",
    "admit",
    "build_task_set",
    "eval_routines",
    "main",
    "write_task_set",
]

SCHEMA = "rosetta.bench.taskset/1"

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "data" / "tasks" / "eval_tasks.json"

#: Divergence kinds that satisfy section 9 rule 3. An M error names what broke
#: and is a legitimate divergence; a bare timeout does not name anything.
SPECIFIC_KINDS: frozenset[str] = frozenset({"output", "global", "error"})

#: Verifier names that did not execute anything. Never publishable.
STUB_VALIDATORS: frozenset[str] = frozenset({"static-stub", ""})

MIN_CASES_FALLBACK = 5


class TaskSetRefused(RuntimeError):
    """The task set is not fit to publish. Raised instead of writing a bad one."""


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class BuildConfig:
    """Everything that determines the contents of a task set."""

    lock_path: Path = split_mod.DEFAULT_LOCK
    candidates_path: Path = split_mod.DEFAULT_CANDIDATES
    routines_dir: Path = split_mod.DEFAULT_ROUTINES
    out_path: Path = DEFAULT_OUT

    verifier: str = "core"
    operators: tuple[str, ...] | None = None
    n_cases: int = 10
    min_cases: int = MIN_CASES_FALLBACK
    max_per_routine: int | None = 8
    max_routines: int | None = None
    target_tasks: int | None = None
    routines: tuple[str, ...] | None = None
    #: Escape hatch for pipeline smoke tests. Stamps the artifact unpublishable.
    allow_unvalidated: bool = False


class _Generator(Protocol):
    """The slice of ``rosetta.mutate.generate.generate_for_routine`` we use."""

    def __call__(
        self,
        name: str,
        src: str,
        verifier: Any,
        *,
        operators: list[str] | None = ...,
        n_cases: int = ...,
        max_tasks: int | None = ...,
        allow_timeout_only: bool = ...,
    ) -> Any: ...


# ----------------------------------------------------------------------
# Admission
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Admission:
    """The bench's verdict on one candidate task."""

    task_id: str
    admitted: bool
    reason: str
    weak: bool = False

    @property
    def ok(self) -> bool:
        return self.admitted


def _kinds(task: Any) -> set[str]:
    return {str(k) for k in getattr(task, "divergence_kinds", ()) or ()}


def admit(
    task: Any,
    eval_set: set[str],
    *,
    min_cases: int = MIN_CASES_FALLBACK,
    allow_unvalidated: bool = False,
    seen_mutants: set[str] | None = None,
) -> Admission:
    """Apply the section 9 validity rules to one generated task.

    ``task`` is a ``rosetta.mutate.generate.MutationTask``; it is typed loosely
    so this gate does not break when that dataclass gains fields.
    """
    task_id = str(task.task_id)

    if str(task.routine).upper() not in eval_set:
        return Admission(task_id, False, "not_in_eval_split")

    validated_by = str(getattr(task, "validated_by", ""))
    if validated_by in STUB_VALIDATORS and not allow_unvalidated:
        return Admission(task_id, False, f"unvalidated:{validated_by or 'none'}")

    if len(task.cases) < min_cases:
        return Admission(task_id, False, "too_few_cases")

    if int(getattr(task, "n_diverged", 0)) < 1:
        return Admission(task_id, False, "no_case_separates")

    kinds = _kinds(task)
    if not kinds & SPECIFIC_KINDS:
        # Killable, but only by a timeout. Section 9 rule 3: weak signal, flag
        # separately. It is recorded, not admitted, and not scored.
        return Admission(task_id, False, "timeout_only", weak=True)

    if seen_mutants is not None:
        digest = hashlib.sha256(
            f"{task.routine}\x00{task.mutated_src}".encode("utf-8")
        ).hexdigest()
        if digest in seen_mutants:
            return Admission(task_id, False, "duplicate_mutant")
        seen_mutants.add(digest)

    return Admission(task_id, True, "admitted")


# ----------------------------------------------------------------------
# Routine selection
# ----------------------------------------------------------------------


def eval_routines(
    config: BuildConfig = BuildConfig(),
) -> list[tuple[str, Path]]:
    """Eval-split routines in candidate-rank order, with their source paths.

    Rank order comes from ``candidates.json`` so a truncated build (``--target-
    tasks``) still takes the most tractable routines first, deterministically.
    """
    lock = split_mod.load_lock(config.lock_path)
    eval_names = [str(n).upper() for n in lock["eval"]]
    wanted = set(eval_names)
    if config.routines:
        asked = {r.upper() for r in config.routines}
        missing = asked - wanted
        if missing:
            raise TaskSetRefused(
                f"routines are not in the eval split: {sorted(missing)}. "
                "Only eval-split routines may become benchmark tasks."
            )
        wanted &= asked

    candidates = json.loads(config.candidates_path.read_text())
    rank = {str(c["name"]).upper(): i for i, c in enumerate(candidates["candidates"])}

    ordered = sorted(wanted, key=lambda n: (rank.get(n, len(rank)), n))
    out: list[tuple[str, Path]] = []
    for name in ordered:
        path = config.routines_dir / f"{_on_disk(name)}.m"
        if not path.is_file():
            raise TaskSetRefused(
                f"routine {name} is in the split lock but {path} is missing. "
                "The corpus changed under the lock; do not build against it."
            )
        out.append((name, path))
    if config.max_routines is not None:
        out = out[: config.max_routines]
    return out


def _on_disk(name: str) -> str:
    """``%DTC`` lives on disk as ``_DTC.m``."""
    return "_" + name[1:] if name.startswith("%") else name


# ----------------------------------------------------------------------
# Build
# ----------------------------------------------------------------------


@dataclass
class TaskSet:
    """A built benchmark task set, admitted and rejected alike."""

    tasks: list[Any] = field(default_factory=list)
    weak_tasks: list[Any] = field(default_factory=list)
    rejections: list[dict[str, Any]] = field(default_factory=list)
    routine_sources: dict[str, str] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    routines_attempted: int = 0
    routines_considered: int = 0
    duration_s: float = 0.0

    @property
    def publishable(self) -> bool:
        return bool(self.provenance.get("publishable")) and bool(self.tasks)

    def reason_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rejections:
            key = str(r["reason"])
            out[key] = out.get(key, 0) + 1
        return dict(sorted(out.items()))

    def by_operator(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.tasks:
            out[t.operator] = out.get(t.operator, 0) + 1
        return dict(sorted(out.items()))

    def by_difficulty(self) -> dict[str, int]:
        out: dict[str, int] = {"easy": 0, "medium": 0, "hard": 0}
        for t in self.tasks:
            out[str(t.difficulty)] = out.get(str(t.difficulty), 0) + 1
        return out

    def by_routine(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.tasks:
            out[t.routine] = out.get(t.routine, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _git(*args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _git_commit() -> str | None:
    return _git("rev-parse", "HEAD") or None


def _git_dirty() -> bool | None:
    """True when the working tree differs from the recorded commit.

    A task set built from uncommitted generator code is reproducible only if
    the reader knows that, so the artifact says so.
    """
    status = _git("status", "--porcelain")
    return None if status is None else bool(status.strip())


def build_task_set(
    config: BuildConfig = BuildConfig(),
    *,
    verifier: Any | None = None,
    generate: _Generator | None = None,
    progress: Callable[[str], None] | None = None,
) -> TaskSet:
    """Generate, gate and assemble the eval task set.

    ``verifier`` and ``generate`` are injectable so this function is testable
    without a container; the defaults are the real ones and the artifact records
    which was used.
    """
    lock = split_mod.load_lock(config.lock_path)
    if not split_mod.verify_lock(config.lock_path, config.routines_dir):
        raise TaskSetRefused(
            "split.lock.json no longer matches the corpus on disk. Every task "
            "set is measured against a specific split; refusing to build."
        )
    eval_set = {str(n).upper() for n in lock["eval"]}

    if generate is None:
        from rosetta.mutate.generate import generate_for_routine as generate  # noqa: PLC0415
    if verifier is None:
        from rosetta.mutate.verify import resolve_verifier  # noqa: PLC0415

        verifier = resolve_verifier(config.verifier)

    routines = eval_routines(config)
    result = TaskSet(routines_considered=len(routines))
    seen_mutants: set[str] = set()
    started = time.monotonic()

    for name, path in routines:
        if config.target_tasks is not None and len(result.tasks) >= config.target_tasks:
            break
        src = path.read_text(errors="replace")
        result.routine_sources[name] = hashlib.sha256(src.encode("utf-8")).hexdigest()
        result.routines_attempted += 1
        if progress:
            progress(f"{name} ({len(result.tasks)} tasks so far)")

        generated = generate(
            name,
            src,
            verifier,
            operators=list(config.operators) if config.operators else None,
            n_cases=config.n_cases,
            max_tasks=config.max_per_routine,
            # The bench decides what a weak signal means, not the generator.
            allow_timeout_only=True,
        )

        for rej in generated.rejections:
            result.rejections.append(
                {
                    "routine": rej.routine,
                    "operator": rej.operator,
                    "detail": rej.detail,
                    "line_no": rej.line_no,
                    "reason": rej.reason,
                    "stage": "generate",
                }
            )

        for task in generated.tasks:
            verdict = admit(
                task,
                eval_set,
                min_cases=config.min_cases,
                allow_unvalidated=config.allow_unvalidated,
                seen_mutants=seen_mutants,
            )
            if verdict.admitted:
                result.tasks.append(task)
            elif verdict.weak:
                result.weak_tasks.append(task)
                result.rejections.append(
                    {
                        "routine": task.routine,
                        "operator": task.operator,
                        "detail": task.detail,
                        "line_no": task.line_no,
                        "reason": verdict.reason,
                        "stage": "admit",
                    }
                )
            else:
                result.rejections.append(
                    {
                        "routine": task.routine,
                        "operator": task.operator,
                        "detail": getattr(task, "detail", ""),
                        "line_no": task.line_no,
                        "reason": verdict.reason,
                        "stage": "admit",
                    }
                )

    result.duration_s = round(time.monotonic() - started, 3)
    validators = sorted({str(getattr(t, "validated_by", "")) for t in result.tasks})
    result.provenance = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "git_dirty": _git_dirty(),
        "python": sys.version.split()[0],
        "split_hash": lock["content_hash"],
        "split_lock": {
            "path": _rel(config.lock_path),
            "schema": lock.get("schema"),
            "counts": lock.get("counts"),
        },
        "candidates": _rel(config.candidates_path),
        "routines_dir": _rel(config.routines_dir),
        "verifier": config.verifier,
        "validated_by": validators,
        "operators": list(config.operators) if config.operators else "all",
        "n_cases": config.n_cases,
        "min_cases": config.min_cases,
        "max_per_routine": config.max_per_routine,
        "max_routines": config.max_routines,
        "target_tasks": config.target_tasks,
        "duration_s": result.duration_s,
        "publishable": bool(validators) and not (set(validators) & STUB_VALIDATORS),
        "reproduce": (
            "python -m rosetta.bench.build --verifier "
            f"{config.verifier} --n-cases {config.n_cases}"
        ),
    }
    return result


def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def write_task_set(result: TaskSet, path: Path | str | None = None) -> Path:
    """Write the task set. Refuses to write an empty or unpublishable one."""
    from rosetta.mutate.generate import task_to_dict  # noqa: PLC0415

    out = Path(path) if path is not None else DEFAULT_OUT
    if out.resolve() == split_mod.DEFAULT_LOCK.resolve():
        raise TaskSetRefused("refusing to write over the split lock")
    if not result.tasks:
        raise TaskSetRefused(
            "no tasks were admitted; not writing an empty task set. "
            f"rejections: {result.reason_counts()}"
        )

    doc = {
        "schema": SCHEMA,
        "provenance": result.provenance,
        "counts": {
            "tasks": len(result.tasks),
            "weak_tasks": len(result.weak_tasks),
            "rejected": len(result.rejections),
            "routines_considered": result.routines_considered,
            "routines_attempted": result.routines_attempted,
            "routines_with_tasks": len(result.by_routine()),
            "by_operator": result.by_operator(),
            "by_difficulty": result.by_difficulty(),
            "by_routine": result.by_routine(),
            "by_reason": result.reason_counts(),
        },
        "routine_sources": result.routine_sources,
        "tasks": [task_to_dict(t) for t in result.tasks],
        "weak_tasks": [task_to_dict(t) for t in result.weak_tasks],
        "rejections": result.rejections,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1) + "\n")
    return out


def load_task_set(path: Path | str = DEFAULT_OUT) -> dict[str, Any]:
    """Read a task set artifact. Used by the scorer to check provenance."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"{p} not found. Run python -m rosetta.bench.build.")
    doc = json.loads(p.read_text())
    if doc.get("schema") != SCHEMA:
        raise TaskSetRefused(f"{p}: expected schema {SCHEMA}, got {doc.get('schema')!r}")
    return doc


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _summarise(result: TaskSet, stream: Any = sys.stderr) -> None:
    print(
        f"admitted {len(result.tasks)} tasks from "
        f"{result.routines_attempted}/{result.routines_considered} eval routines "
        f"in {result.duration_s}s",
        file=stream,
    )
    print(f"  by operator   {result.by_operator()}", file=stream)
    print(f"  by difficulty {result.by_difficulty()}", file=stream)
    print(
        f"  weak (timeout-only, not admitted) {len(result.weak_tasks)}",
        file=stream,
    )
    print(f"  rejected {len(result.rejections)}: {result.reason_counts()}", file=stream)
    if not result.provenance.get("publishable"):
        print(
            "  WARNING: not publishable -- tasks were not validated by rosetta.core",
            file=stream,
        )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rosetta.bench.build", description=__doc__)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--verifier", choices=("core", "static"), default="core")
    ap.add_argument("--operators", help="comma-separated operator subset")
    ap.add_argument("--routines", help="comma-separated eval-split routine names")
    ap.add_argument("--n-cases", type=int, default=10)
    ap.add_argument("--max-per-routine", type=int, default=8)
    ap.add_argument("--max-routines", type=int)
    ap.add_argument("--target-tasks", type=int)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="list the eval routines that would be attempted, then stop",
    )
    ap.add_argument(
        "--allow-unvalidated",
        action="store_true",
        help="permit stub-validated tasks; the artifact is stamped unpublishable",
    )
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    config = BuildConfig(
        out_path=Path(args.out),
        verifier=args.verifier,
        operators=(
            tuple(o.strip().upper() for o in args.operators.split(",") if o.strip())
            if args.operators
            else None
        ),
        routines=(
            tuple(r.strip().upper() for r in args.routines.split(",") if r.strip())
            if args.routines
            else None
        ),
        n_cases=args.n_cases,
        max_per_routine=args.max_per_routine,
        max_routines=args.max_routines,
        target_tasks=args.target_tasks,
        allow_unvalidated=args.allow_unvalidated,
    )

    if args.dry_run:
        routines = eval_routines(config)
        for name, path in routines:
            print(f"{name}\t{_rel(path)}")
        print(f"{len(routines)} eval routines", file=sys.stderr)
        return 0

    if args.verifier == "static":
        print(
            "WARNING: --verifier static executes nothing. Equivalent mutants are "
            "NOT filtered and the result is not a benchmark.",
            file=sys.stderr,
        )

    result = build_task_set(
        config,
        progress=None if args.quiet else lambda m: print(f"  {m}", file=sys.stderr),
    )
    _summarise(result)
    path = write_task_set(result, config.out_path)
    print(f"wrote {_rel(path)}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
