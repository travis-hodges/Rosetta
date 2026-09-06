"""Live progress for a benchmark run.

`run.py` streams TraceRecords to JSONL as they complete, so progress is
readable from the trace itself without instrumenting the runner or waiting
for it to finish. Safe to run against a file that is still being written.

    python3 -m rosetta.bench.progress                  # newest run
    python3 -m rosetta.bench.progress --watch          # refresh until done
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


# --- Rosetta palette, matching web/index.html -------------------------------
# accent #ff8053, verified green #c4deaa, muted #a1a49a, ink #f4f1e9, line #343730
_RGB = {
    "accent": (255, 128, 83),
    "green": (196, 222, 170),
    "muted": (161, 164, 154),
    "ink": (244, 241, 233),
    "line": (52, 55, 48),
    "red": (255, 95, 86),
}


def _supports_color(stream: Any = None) -> bool:
    """Colour only when it will actually render.

    Honours NO_COLOR (informal standard) and skips escapes when the output is
    redirected, so a piped run does not fill a log with control characters.
    """
    import os

    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    stream = stream or sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)())


class _Paint:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, colour: str, bold: bool = False) -> str:
        if not self.enabled:
            return text
        r, g, b = _RGB[colour]
        prefix = "\033[1m" if bold else ""
        return f"{prefix}\033[38;2;{r};{g};{b}m{text}\033[0m"


WORDMARK = ("███████",
            "█████",
            "███")


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = REPO_ROOT / "results" / "bench"
DEFAULT_TASKSET = REPO_ROOT / "data" / "tasks" / "eval_tasks.json"


@dataclass
class Progress:
    run_id: str
    done: int
    total: int
    by_condition: dict[str, int]
    mean_s: float
    median_s: float
    elapsed_s: float
    passed: int
    asserted: int
    false_confident: int

    @property
    def remaining(self) -> int:
        return max(self.total - self.done, 0)

    @property
    def eta_s(self) -> float:
        """Wall-clock estimate from observed throughput, not per-task cost.

        Using mean task duration would ignore that tasks run back to back;
        elapsed/done is what actually predicts the finish.
        """
        if self.done == 0:
            return float("nan")
        return self.remaining * (self.elapsed_s / self.done)


def _newest(directory: Path) -> Path:
    files = sorted(directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise FileNotFoundError(f"no trace files in {directory}")
    return files[-1]


def _read(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a partially flushed final line, not corruption
    return out


def _elapsed(recs: list[dict[str, Any]]) -> float:
    """Wall-clock since the first record started.

    Not the file's ctime: on macOS that is the inode change time, which the
    streaming writer updates on every flush, so it always reads as seconds old.
    """
    starts = [r["started_at"] for r in recs if r.get("started_at")]
    if not starts:
        return 0.0
    try:
        first = dt.datetime.fromisoformat(min(starts))
    except ValueError:
        return 0.0
    now = dt.datetime.now(first.tzinfo) if first.tzinfo else dt.datetime.now()
    return (now - first).total_seconds()


def measure(path: Path, taskset: Path, conditions: int = 2) -> Progress:
    recs = _read(path)
    total_tasks = 0
    if taskset.exists():
        total_tasks = len(json.loads(taskset.read_text()).get("tasks", []))

    durs: list[float] = []
    for r in recs:
        s, f = r.get("started_at"), r.get("finished_at")
        if s and f:
            try:
                durs.append(
                    (dt.datetime.fromisoformat(f) - dt.datetime.fromisoformat(s)).total_seconds()
                )
            except ValueError:
                pass

    by_cond: dict[str, int] = {}
    for r in recs:
        by_cond[r["condition"]] = by_cond.get(r["condition"], 0) + 1

    firsts = [r for r in recs if r.get("attempt") == 1]
    passed = sum(1 for r in firsts if (r.get("verdict") or {}).get("equivalent"))
    asserted = sum(
        1 for r in firsts if (r.get("assertion") or {}).get("claimed_correct")
    )
    false_conf = sum(
        1 for r in firsts
        if (r.get("assertion") or {}).get("claimed_correct")
        and not (r.get("verdict") or {}).get("equivalent")
        and r.get("verdict") is not None
    )

    return Progress(
        run_id=path.stem,
        done=len(firsts),
        total=total_tasks * conditions,
        by_condition=by_cond,
        mean_s=statistics.mean(durs) if durs else 0.0,
        median_s=statistics.median(durs) if durs else 0.0,
        elapsed_s=_elapsed(recs),
        passed=passed,
        asserted=asserted,
        false_confident=false_conf,
    )


def _bar(done: int, total: int, paint: "_Paint", width: int = 34) -> str:
    if total <= 0:
        return paint("?" * width, "muted")
    filled = int(width * done / total)
    return paint("█" * filled, "accent") + paint("━" * (width - filled), "line")


def _hms(seconds: float) -> str:
    if seconds != seconds:  # NaN
        return "unknown"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def render(p: Progress, colour: bool | None = None) -> str:
    paint = _Paint(_supports_color() if colour is None else colour)
    pct = 100 * p.done / p.total if p.total else 0
    stalled = p.eta_s != p.eta_s

    head = [
        f"  {paint(WORDMARK[0], 'accent')}",
        f"  {paint(WORDMARK[1], 'accent')}   {paint('R O S E T T A', 'ink', bold=True)}"
        f"   {paint('verification benchmark', 'muted')}",
        f"  {paint(WORDMARK[2], 'accent')}   {paint(p.run_id, 'muted')}",
        "",
    ]

    body = [
        f"  {_bar(p.done, p.total, paint)}  "
        f"{paint(f'{p.done}/{p.total}', 'ink', bold=True)} "
        f"{paint(f'{pct:.0f}%', 'accent')}",
        f"  {paint('elapsed', 'muted')} {_hms(p.elapsed_s)}"
        f"   {paint('eta', 'muted')} "
        f"{paint(_hms(p.eta_s), 'muted' if stalled else 'ink')}",
        f"  {paint('per task', 'muted')} median {p.median_s:.0f}s  mean {p.mean_s:.0f}s",
        f"  {paint('conditions', 'muted')} "
        + "  ".join(f"{k} {paint(str(v), 'ink')}" for k, v in sorted(p.by_condition.items())),
    ]

    if p.done:
        fc_colour = "green" if p.false_confident == 0 else "red"
        body += [
            "",
            f"  {paint('running tally', 'muted')}  "
            f"pass@1 {paint(f'{p.passed}/{p.done}', 'green')}   "
            f"asserted {paint(str(p.asserted), 'ink')}   "
            f"false-confident {paint(str(p.false_confident), fc_colour)}",
            f"  {paint('partial and unstratified -- not a publishable result', 'muted')}",
        ]
    return "\n".join(head + body)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--file", type=Path, default=None)
    ap.add_argument("--taskset", type=Path, default=DEFAULT_TASKSET)
    ap.add_argument("--conditions", type=int, default=2)
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--interval", type=float, default=30.0)
    args = ap.parse_args(argv)

    while True:
        path = args.file or _newest(args.dir)
        p = measure(path, args.taskset, args.conditions)
        if args.watch:
            print("\033[2J\033[H", end="")
        print(render(p, colour=False if args.no_color else None))
        if not args.watch or p.done >= p.total:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
