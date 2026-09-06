"""The side-by-side. Left: no verifier, fluent and wrong. Right: verifier, red.

This is the one moment docs/PROJECT.md #12 says to engineer, and #8 says it has
to run "reliably, twice in a row, cold start, no network". So:

  * The canned path is primary. It renders a **recorded audit trace** -- the
    same ``results/*.jsonl`` the repair loop writes -- so what the audience
    sees is a real verifier run, replayed, not a script of what a verifier
    would have said.
  * The live path (``--live``) re-runs the repair loop against the container
    and is gated behind the canned path: if anything at all goes wrong it
    prints one line and falls back to the recording.
  * Nothing in this module raises in front of an audience. A missing file, a
    malformed trace, a dead container and a 40-column terminal all degrade to
    something readable. Library modules fail loudly; this one does not.

Stdlib only, no network, no curses.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
import textwrap
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence, TextIO

from rosetta.proof import trace_digest

from .tasks import repo_root

__all__ = ["Recording", "load_trace", "render", "main"]

CANNED_DIR = "results/canned"

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
CYAN = "\x1b[36m"
GREY = "\x1b[90m"


@dataclass
class AttemptView:
    """One candidate and, if the agent was allowed to see it, its verdict."""

    n: int
    diff: str = ""
    explanation: str = ""
    verdict: dict[str, Any] | None = None
    agent_visible: bool = False
    tool_calls: int = 0


@dataclass
class ConditionView:
    condition: str
    backend: str = "?"
    attempts: list[AttemptView] = field(default_factory=list)
    claimed_success: bool = False
    verified_equivalent: bool | None = None
    error: str | None = None
    tool_calls: int = 0


@dataclass
class Recording:
    """A parsed trace: everything the renderer needs, and nothing else."""

    path: str = ""
    run_id: str = ""
    recorded_at: float | None = None
    baseline_sha1: str = ""
    task_id: str = ""
    routine: str = ""
    title: str = ""
    request: str = ""
    stakes: str = ""
    backend: str = ""
    model: str | None = None
    conditions: dict[str, ConditionView] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return "tools_off" in self.conditions and "tools_on" in self.conditions

    @property
    def has_money_moment(self) -> bool:
        """True only if this recording actually contains the contrast.

        A live run against a dead container still writes a well-formed trace --
        one with no verdicts in it. Rendering that would put an empty right-hand
        column on the screen, which is worse than showing the recording. The
        ``--live`` path uses this to decide whether to keep what it just made.
        """
        if not self.usable:
            return False
        off = self.conditions["tools_off"]
        on = self.conditions["tools_on"]
        if off.verified_equivalent is not False:
            return False
        first = on.attempts[0] if on.attempts else None
        if first is None or not first.verdict:
            return False
        return any(
            case.get("divergences") for case in first.verdict.get("cases", [])
        )

    @property
    def verifier_live(self) -> bool:
        """Whether the trace contains verdicts produced by real execution."""
        return any(
            attempt.verdict and attempt.verdict.get("source") == "live"
            for condition in self.conditions.values()
            for attempt in condition.attempts
        )

    @property
    def n_cases(self) -> int:
        return max(
            (
                int(attempt.verdict.get("n_cases", 0))
                for condition in self.conditions.values()
                for attempt in condition.attempts
                if attempt.verdict
            ),
            default=0,
        )

    @property
    def n_void(self) -> int:
        return max(
            (
                int(attempt.verdict.get("n_void", 0))
                for condition in self.conditions.values()
                for attempt in condition.attempts
                if attempt.verdict
            ),
            default=0,
        )


def _condition(rec: Recording, name: str) -> ConditionView:
    return rec.conditions.setdefault(name, ConditionView(condition=name))


def load_trace(path: str | Path) -> Recording:
    """Parse a repair-loop JSONL trace. Never raises; collects problems."""
    rec = Recording(path=str(path))
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        rec.problems.append(f"could not read trace: {exc}")
        return rec

    for lineno, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError as exc:
            rec.problems.append(f"line {lineno}: {exc}")
            continue

        kind = d.get("kind")
        if kind == "run_start":
            rec.run_id = d.get("run_id", "")
            rec.recorded_at = d.get("ts")
            rec.baseline_sha1 = d.get("baseline_sha1", "")
            rec.task_id = d.get("task_id", "")
            rec.routine = d.get("routine", "")
            rec.title = d.get("title", "")
            rec.request = d.get("request", "")
            rec.stakes = d.get("stakes", "")
            rec.backend = d.get("backend", "")
            rec.model = d.get("model")
        elif kind == "condition_start":
            cond = _condition(rec, d.get("condition", "?"))
            cond.backend = d.get("backend", "?")
            rec.request = rec.request or d.get("request", "")
            rec.routine = rec.routine or d.get("routine", "")
            rec.task_id = rec.task_id or d.get("task_id", "")
        elif kind == "candidate":
            cond = _condition(rec, d.get("condition", "?"))
            cond.attempts.append(
                AttemptView(
                    n=int(d.get("attempt", len(cond.attempts) + 1)),
                    diff=d.get("diff", ""),
                    explanation=d.get("explanation", ""),
                )
            )
        elif kind == "tool_call":
            cond = _condition(rec, d.get("condition", "?"))
            cond.tool_calls += 1
            if cond.attempts:
                cond.attempts[-1].tool_calls += 1
        elif kind == "verdict":
            cond = _condition(rec, d.get("condition", "?"))
            target = None
            for att in cond.attempts:
                if att.n == d.get("attempt"):
                    target = att
            if target is None and cond.attempts:
                target = cond.attempts[-1]
            if target is not None:
                target.verdict = d
                target.agent_visible = bool(d.get("agent_visible"))
        elif kind == "condition_end":
            cond = _condition(rec, d.get("condition", "?"))
            cond.claimed_success = bool(d.get("agent_claimed_success"))
            cond.verified_equivalent = d.get("verified_equivalent")
            cond.error = d.get("error")

    if not rec.usable:
        rec.problems.append("trace does not contain both conditions")
    return rec


# --------------------------------------------------------------------- paint


class Painter:
    """Two-column layout with graceful collapse on narrow terminals."""

    def __init__(
        self,
        stream: TextIO,
        color: bool,
        width: int | None = None,
        pace: float = 0.0,
    ) -> None:
        self.out = stream
        self.color = color
        self.pace = max(pace, 0.0)
        self.width = width or self._detect_width()
        self.split = self.width >= 96
        self.col = (self.width - 3) // 2 if self.split else self.width

    @staticmethod
    def _detect_width() -> int:
        try:
            return max(shutil.get_terminal_size((100, 24)).columns, 40)
        except OSError:  # pragma: no cover
            return 100

    def c(self, text: str, *codes: str) -> str:
        if not self.color or not codes:
            return text
        return "".join(codes) + text + RESET

    def line(self, text: str = "") -> None:
        self.out.write(text + "\n")
        self.out.flush()

    def beat(self) -> None:
        if self.pace:
            time.sleep(self.pace)

    def rule(self, char: str = "─") -> None:
        self.line(self.c(char * self.width, GREY))

    def wrap(self, text: str, width: int, indent: str = "") -> list[str]:
        out: list[str] = []
        for para in (text or "").splitlines() or [""]:
            if not para.strip():
                out.append("")
                continue
            out.extend(
                textwrap.wrap(
                    para,
                    width=max(width - len(indent), 12),
                    initial_indent=indent,
                    subsequent_indent=indent,
                )
                or [indent]
            )
        return out

    @staticmethod
    def _visible_len(text: str) -> int:
        out, i = 0, 0
        while i < len(text):
            if text[i] == "\x1b":
                j = text.find("m", i)
                if j == -1:
                    break
                i = j + 1
                continue
            out += 1
            i += 1
        return out

    def columns(self, left: Sequence[str], right: Sequence[str]) -> None:
        """Print two blocks side by side, or stacked if the terminal is narrow."""
        if not self.split:
            for row in left:
                self.line(row)
            self.line()
            for row in right:
                self.line(row)
            return
        height = max(len(left), len(right))
        bar = self.c("│", GREY)
        for i in range(height):
            lft = left[i] if i < len(left) else ""
            rgt = right[i] if i < len(right) else ""
            pad = max(self.col - self._visible_len(lft), 0)
            self.line(f"{lft}{' ' * pad} {bar} {rgt}")


def _headline(painter: Painter, cond: ConditionView, tools_on: bool) -> list[str]:
    if tools_on:
        return [
            painter.c("  TOOLS ON   ", BOLD, CYAN)
            + painter.c("agent + Rosetta verifier", GREY),
        ]
    return [
        painter.c("  TOOLS OFF  ", BOLD, YELLOW)
        + painter.c("agent with file access only", GREY),
    ]


def _diff_block(painter: Painter, diff: str, width: int) -> list[str]:
    rows: list[str] = []
    for raw in (diff or "").splitlines():
        text = raw[: width - 2]
        if raw.startswith("+++") or raw.startswith("---"):
            rows.append("  " + painter.c(text, GREY))
        elif raw.startswith("@@"):
            rows.append("  " + painter.c(text, CYAN))
        elif raw.startswith("-"):
            rows.append("  " + painter.c(text, RED))
        elif raw.startswith("+"):
            rows.append("  " + painter.c(text, GREEN))
        else:
            rows.append("  " + painter.c(text, DIM))
    return rows


def _verdict_block(
    painter: Painter, verdict: dict[str, Any] | None, width: int
) -> list[str]:
    if not verdict:
        return [painter.c("  (no verdict — the agent never asked)", GREY)]
    rows: list[str] = []
    if verdict.get("equivalent"):
        rows.append(painter.c("  ✓ EQUIVALENT", BOLD, GREEN))
        rows.append(
            painter.c(
                f"    matched the baseline on all {verdict.get('n_cases', '?')} case(s)",
                GREEN,
            )
        )
        return rows

    rows.append(painter.c("  ✗ NOT EQUIVALENT", BOLD, RED))
    rows.append(
        painter.c(
            f"    diverged on {verdict.get('n_diverged', '?')} of "
            f"{verdict.get('n_cases', '?')} case(s)",
            RED,
        )
    )
    rows.append("")
    for case in verdict.get("cases", []):
        if case.get("equivalent"):
            continue
        rows.append(painter.c(f"    {case.get('label', '?')}", BOLD))
        rows.extend(painter.wrap(case.get("why", ""), width, indent="      "))
        for div in case.get("divergences", []):
            ref = div.get("ref", "?")
            rows.append(
                "      "
                + painter.c(f"{div.get('kind', '?')} {ref}", CYAN)
            )
            rows.append(
                "        expected " + painter.c(json.dumps(div.get("expected", "")), GREEN)
            )
            rows.append(
                "        actual   " + painter.c(json.dumps(div.get("actual", "")), RED)
            )
        rows.append("")
    return rows


def render(
    rec: Recording,
    *,
    stream: TextIO | None = None,
    color: bool | None = None,
    width: int | None = None,
    pace: float = 0.0,
    show_repair: bool = True,
    live_now: bool = False,
) -> int:
    """Paint the side-by-side. Returns 0 unless the recording was unusable."""
    stream = stream or sys.stdout
    if color is None:
        color = stream.isatty() and not os.environ.get("NO_COLOR")
    p = Painter(stream, color, width, pace)

    if not rec.usable:
        p.line(p.c("side-by-side unavailable", BOLD, RED))
        for problem in rec.problems:
            p.line(f"  {problem}")
        p.line(
            "  Record one with: python3 -m rosetta.demo.repair_loop "
            "--task nok-ajetiu2"
        )
        return 1

    off = rec.conditions["tools_off"]
    on = rec.conditions["tools_on"]

    p.line()
    provenance = (
        "LIVE YOTTADB · EXECUTED NOW"
        if live_now and rec.verifier_live
        else "RECORDED AUDIT TRACE · LIVE YOTTADB CAPTURE"
        if rec.verifier_live
        else "RECORDED TRACE · EXECUTION NOT PROVEN"
    )
    p.line(p.c(f"  ◉ {provenance}", BOLD, GREEN if rec.verifier_live else YELLOW))
    p.line(p.c(f"  ROSETTA — {rec.title}", BOLD))
    p.line(
        p.c(
            f"  routine {rec.routine}    task {rec.task_id}    "
            f"candidate generator {rec.backend or '?'}"
            + (f"    model {rec.model}" if rec.model else ""),
            GREY,
        )
    )
    p.rule()
    for row in p.wrap(rec.request, p.width - 4, indent="  "):
        p.line(row)
    p.rule()
    p.beat()

    # --- the change, identical on both sides -----------------------------
    diff = (off.attempts[0].diff if off.attempts else "") or (
        on.attempts[0].diff if on.attempts else ""
    )
    p.line(p.c("  THE CHANGE — one line, and both agents wrote the same one", BOLD))
    p.line()
    for row in _diff_block(p, diff, p.width):
        p.line(row)
    p.line()
    p.beat()
    p.rule()

    # --- the frame -------------------------------------------------------
    left = _headline(p, off, tools_on=False)
    right = _headline(p, on, tools_on=True)
    left.append("")
    right.append("")

    if off.attempts:
        left.append(p.c("  The agent's own account:", GREY))
        left.extend(p.wrap(off.attempts[0].explanation, p.col, indent="    "))
        left.append("")
        left.append(p.c("  ✓ done — no issues found", BOLD, GREEN))
        left.append(p.c("    (nothing was executed; nothing was checked)", GREY))
    else:
        left.append(p.c("  the agent produced nothing", RED))

    first = on.attempts[0] if on.attempts else None
    right.extend(
        p.c(row, GREY)
        for row in p.wrap(
            f"verify_change over MCP — {first.tool_calls if first else 0} call(s), "
            "real routine, real database",
            p.col,
            indent="  ",
        )
    )
    right.append("")
    right.extend(_verdict_block(p, first.verdict if first else None, p.col))

    p.line()
    p.columns(left, right)
    p.beat()

    # --- the stakes ------------------------------------------------------
    if rec.stakes:
        p.rule()
        for row in p.wrap(rec.stakes, p.width - 4, indent="  "):
            p.line(p.c(row, YELLOW))
    p.rule()

    hidden = next(
        (a.verdict for a in off.attempts if a.verdict and not a.agent_visible), None
    )
    if hidden is not None and not hidden.get("equivalent"):
        p.line(
            p.c(
                "  Left shipped it. Rosetta scored the same change afterwards: "
                f"{hidden.get('n_diverged')} of {hidden.get('n_cases')} cases wrong.",
                BOLD,
                RED,
            )
        )
        p.line(
            p.c(
                "  That is the false-confidence rate. It is the whole product.",
                BOLD,
            )
        )
        p.rule()
    p.beat()

    # --- the repair ------------------------------------------------------
    repaired = next(
        (a for a in on.attempts[1:] if a.verdict and a.verdict.get("equivalent")), None
    )
    if show_repair and repaired is not None:
        p.line()
        p.line(
            p.c(
                f"  TOOLS ON, attempt {repaired.n} — the agent read the divergence "
                "and repaired it",
                BOLD,
                CYAN,
            )
        )
        p.line()
        for row in _diff_block(p, repaired.diff, p.width):
            p.line(row)
        p.line()
        for row in _verdict_block(p, repaired.verdict, p.width):
            p.line(row)
        p.rule()

    p.line()
    p.line(p.c("  PROOF RECEIPT", BOLD, GREEN if rec.verifier_live else YELLOW))
    p.line(
        "  "
        + p.c("runtime", GREY)
        + f"  {'YottaDB' if rec.verifier_live else 'unproven'}    "
        + p.c("cases", GREY)
        + f"  {rec.n_cases}    "
        + p.c("observables", GREY)
        + "  stdout · errors · persistent globals"
    )
    restored = rec.verifier_live and rec.n_void == 0
    p.line(
        "  "
        + p.c("clean_state", GREY)
        + (p.c("  ROLLBACK CONFIRMED", GREEN) if restored else p.c("  NOT ASSERTED", YELLOW))
    )
    try:
        digest = trace_digest(rec.path)
        p.line("  " + p.c("trace sha256", GREY) + f"  {digest}")
    except OSError as exc:
        p.line("  " + p.c("trace sha256", GREY) + f"  unavailable ({exc})")
    p.line(p.c(f"  trace: {rec.path}", GREY))
    p.line()
    return 0


# ---------------------------------------------------------------- selection


def canned_trace(task_id: str = "nok-ajetiu2") -> Path | None:
    """The committed recording for a task, if one is present."""
    root = repo_root()
    exact = root / CANNED_DIR / f"{task_id}.jsonl"
    if exact.is_file():
        return exact
    matches = sorted(glob.glob(str(root / CANNED_DIR / "*.jsonl")))
    return Path(matches[-1]) if matches else None


def latest_trace(task_id: str = "nok-ajetiu2") -> Path | None:
    """The newest live trace under results/, if any."""
    matches = sorted(
        glob.glob(str(repo_root() / "results" / f"{task_id}-*.jsonl")),
        key=lambda p: os.path.getmtime(p),
    )
    return Path(matches[-1]) if matches else None


class _LivePulse:
    """Small TTY-only progress animation; never claims a stage completed."""

    frames = ("◐", "◓", "◑", "◒")
    messages = (
        "opening clean-state verification frames",
        "executing baseline ↔ candidate",
        "comparing stdout + errors + persistent globals",
    )

    def __init__(self, out: TextIO, enabled: bool) -> None:
        self.out = out
        self.enabled = enabled and bool(getattr(out, "isatty", lambda: False)())
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled:
            return

        def paint() -> None:
            tick = 0
            while not self.stop_event.wait(0.11):
                frame = self.frames[tick % len(self.frames)]
                message = self.messages[(tick // 18) % len(self.messages)]
                self.out.write(f"\r\x1b[2K  {CYAN}{frame}{RESET} LIVE YOTTADB  {message}")
                self.out.flush()
                tick += 1

        self.thread = threading.Thread(target=paint, name="rosetta-proof-pulse", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if not self.enabled:
            return
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=0.5)
        self.out.write("\r\x1b[2K")
        self.out.flush()


def _run_live(task_id: str, out: TextIO, *, animate: bool = True) -> Path | None:
    """Try to record a fresh trace. Returns None on any failure."""
    pulse = _LivePulse(out, animate)
    pulse.start()
    try:
        from .repair_loop import run_task
        from .tasks import get_task

        summary = run_task(get_task(task_id), backend="scripted")
        return Path(summary["trace"])
    except Exception as exc:  # demo path: never propagate
        out.write(f"live run unavailable ({type(exc).__name__}: {exc}); "
                  "falling back to the recording\n")
        return None
    finally:
        pulse.stop()


def _why_not(rec: Recording) -> str:
    """One short line explaining why a recording is not showable."""
    if not rec.usable:
        return "; ".join(rec.problems) or "incomplete trace"
    for name in ("tools_off", "tools_on"):
        cond = rec.conditions[name]
        if cond.error:
            return f"{name}: {cond.error}"
    return "no verdict was reached"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m rosetta.demo.sidebyside",
        description="Render the Rosetta side-by-side from an audit trace.",
    )
    parser.add_argument("--task", default="nok-ajetiu2")
    parser.add_argument("--trace", default=None, help="explicit trace file to render")
    parser.add_argument(
        "--live",
        action="store_true",
        help="record a fresh trace first (needs the container); falls back to canned",
    )
    parser.add_argument("--pace", type=float, default=None, help="seconds between beats")
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--no-repair", action="store_true")
    parser.add_argument("--no-animation", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    rec: Recording | None = None
    live_now = False
    if args.trace:
        rec = load_trace(Path(args.trace))
    elif args.live:
        live = _run_live(args.task, sys.stderr, animate=not args.no_animation)
        if live is not None:
            candidate = load_trace(live)
            # A live run that reached no verdict is worse than the recording.
            if candidate.has_money_moment:
                rec = candidate
                live_now = True
            else:
                sys.stderr.write(
                    "live run produced no divergence to show "
                    f"({_why_not(candidate)}); falling back to the recording\n"
                )

    if rec is None:
        path = canned_trace(args.task) or latest_trace(args.task)
        if path is None:
            sys.stdout.write(
                "no recording found. Make one with:\n"
                f"  python3 -m rosetta.demo.repair_loop --task {args.task}\n"
                f"  cp results/*.jsonl {CANNED_DIR}/{args.task}.jsonl\n"
            )
            return 1
        rec = load_trace(path)

    pace = args.pace
    if pace is None:
        pace = 0.10 if sys.stdout.isatty() and not args.no_animation else 0.0
    return render(
        rec,
        color=False if args.no_color else None,
        width=args.width,
        pace=pace,
        show_repair=not args.no_repair,
        live_now=live_now,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
