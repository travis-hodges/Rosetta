"""Turning a ``VerifyReport`` into feedback an agent can act on.

This is the single most important interface decision in the tool stream.
``verify_change`` must not answer "false". A repair loop cannot do anything with
a boolean. It needs to be told *what moved*: which global, which node, which
``^``-delimited piece, which FileMan field that piece is, and what the value was
before and after.

So every divergence is rendered three ways:

* structurally, as the frozen-contract :class:`Divergence` fields
* semantically, via the FileMan dictionary — ``piece 2 (SEX, field .02 of file
  #2 PATIENT): expected "M" (MALE), got "" (<empty>)``
* as prose in ``feedback``, which is what the model reads first

``VerifyReport.summary()`` is declared but unimplemented in the frozen contract,
so nothing here calls it.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any

from rosetta.core.interface import Divergence, ExecResult, VerifyReport

from .fileman import DataDictionary

__all__ = [
    "M_ERRORS",
    "ExplainedDivergence",
    "RenderedReport",
    "explain_divergence",
    "render_report",
    "render_exec_result",
]

#: MUMPS runtime error codes. An error is a divergence, and it is often the
#: entire signal, so the code must be legible to a reader who has never seen M.
M_ERRORS: dict[str, str] = {
    "M6": "undefined local variable — the routine read a local that was never set",
    "M7": "undefined global variable — the routine read a ^global node that does not exist",
    "M9": "divide by zero",
    "M10": "invalid pattern match",
    "M11": "no parameters passed to a routine that requires them",
    "M13": "line reference less than zero",
    "M15": "duplicate NEW / exclusive-KILL of an undefined name",
    "M26": "non-existent block referenced",
    "M28": "argument-less command given an argument",
    "M56": "identifier exceeds the maximum length",
    "M57": "more than one label of the same name in a routine",
    "M58": "too few arguments for a formal parameter list",
    "ZLINKFILE": "routine source failed to compile",
    "HALT": "the routine executed HALT and killed the worker process",
    "TIMEOUT": "the case exceeded its timeout_s budget",
}

_KIND_LEAD = {
    "output": "Written output differs",
    "global": "Database state differs",
    "error": "Runtime error differs",
    "timeout": "Execution timed out",
}


def _describe_error(code: str) -> str:
    if not code:
        return "no error"
    base = code.split(",")[0].split("-")[0].strip().upper()
    meaning = M_ERRORS.get(base) or M_ERRORS.get(code.strip().upper())
    return f"{code} ({meaning})" if meaning else code


def _first_difference(a: str, b: str) -> int:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


def _piece_diff(expected: str, actual: str) -> list[str]:
    """``^``-piece level diff for a global with no FileMan schema."""
    e, a = expected.split("^"), actual.split("^")
    out: list[str] = []
    for i in range(max(len(e), len(a))):
        ev = e[i] if i < len(e) else ""
        av = a[i] if i < len(a) else ""
        if ev != av:
            out.append(f'piece {i + 1}: expected "{ev}", got "{av}"')
    if not out and expected != actual:
        out.append(f'value: expected "{expected}", got "{actual}"')
    return out


@dataclass(frozen=True)
class ExplainedDivergence:
    """A frozen-contract :class:`Divergence` plus everything we can say about it."""

    kind: str
    ref: str
    case_index: int
    expected: str
    actual: str
    headline: str
    details: list[str] = dc_field(default_factory=list)
    fileman_file: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ref": self.ref,
            "case_index": self.case_index,
            "expected": self.expected,
            "actual": self.actual,
            "headline": self.headline,
            "details": list(self.details),
            "fileman_file": self.fileman_file,
        }

    def as_text(self) -> str:
        lines = [f"[case {self.case_index}] {self.headline}"]
        lines += [f"    {d}" for d in self.details]
        return "\n".join(lines)


def explain_divergence(
    div: Divergence, dictionary: DataDictionary | None = None
) -> ExplainedDivergence:
    """Attach FileMan meaning and prose to one divergence."""
    kind = str(div.kind)
    lead = _KIND_LEAD.get(kind, "Divergence")
    details: list[str] = []
    fileman_file = ""

    if kind == "global":
        headline = f"{lead} at {div.ref}"
        if div.expected and not div.actual:
            headline += " — the candidate left this node empty or killed it"
        elif div.actual and not div.expected:
            headline += " — the candidate created a node the baseline never writes"
        if dictionary is not None:
            explained = dictionary.explain_node_change(div.ref, div.expected, div.actual)
            if explained:
                details.extend(explained)
                res = dictionary.resolve(div.ref, sample_limit=0)
                if res.fileman_file is not None:
                    fileman_file = (
                        f"#{res.fileman_file.number} {res.fileman_file.name}"
                    )
        if not details:
            details.extend(_piece_diff(div.expected, div.actual))
            if dictionary is not None and not fileman_file:
                details.append(
                    f"{div.ref.split('(')[0]} has no FileMan data dictionary "
                    "entry; the pieces above are positional only."
                )
    elif kind == "error":
        exp, act = _describe_error(div.expected), _describe_error(div.actual)
        if div.expected and not div.actual:
            headline = (
                f"{lead}: the baseline raises {exp} here and the candidate does not. "
                "Suppressing a real error is a behaviour change, not a fix."
            )
        elif div.actual and not div.expected:
            headline = f"{lead}: the candidate raises {act} where the baseline succeeds"
        else:
            headline = f"{lead}: baseline {exp}, candidate {act}"
        details.append(f"expected error: {exp}")
        details.append(f"actual error:   {act}")
    elif kind == "timeout":
        headline = f"{lead} on {div.ref}: expected {div.expected!r}, got {div.actual!r}"
        details.append(
            "A timeout usually means the candidate turned a bounded $ORDER walk "
            "into an unbounded one, or inverted a loop termination test."
        )
    else:  # output
        idx = _first_difference(div.expected, div.actual)
        headline = f"{lead} ({div.ref})"
        details.append(f'expected: "{div.expected}"')
        details.append(f'actual:   "{div.actual}"')
        if div.expected and div.actual:
            details.append(
                f"first difference at character {idx + 1} of "
                f"{max(len(div.expected), len(div.actual))}"
            )
        if "^" in div.expected or "^" in div.actual:
            details.extend(_piece_diff(div.expected, div.actual))

    return ExplainedDivergence(
        kind=kind,
        ref=div.ref,
        case_index=div.case_index,
        expected=div.expected,
        actual=div.actual,
        headline=headline,
        details=details,
        fileman_file=fileman_file,
    )


@dataclass(frozen=True)
class RenderedReport:
    """Serialisable, model-readable form of a :class:`VerifyReport`."""

    equivalent: bool
    n_cases: int
    n_diverged: int
    n_void: int
    verdict: str
    divergences: list[ExplainedDivergence]
    by_kind: dict[str, int]
    refs_touched: list[str]
    feedback: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "equivalent": self.equivalent,
            "n_cases": self.n_cases,
            "n_diverged": self.n_diverged,
            "n_void": self.n_void,
            "verdict": self.verdict,
            "by_kind": dict(self.by_kind),
            "refs_touched": list(self.refs_touched),
            "divergences": [d.to_dict() for d in self.divergences],
            "feedback": self.feedback,
        }


def render_report(
    report: VerifyReport,
    dictionary: DataDictionary | None = None,
    routine: str = "",
    max_divergences: int = 40,
) -> RenderedReport:
    """Explain a whole verification run.

    ``max_divergences`` bounds what is *rendered*; the counts always reflect the
    full report, so a truncated render can never be mistaken for a clean one.
    """
    divs = list(report.divergences or [])
    explained = [explain_divergence(d, dictionary) for d in divs[:max_divergences]]
    by_kind: dict[str, int] = {}
    refs: list[str] = []
    for d in divs:
        by_kind[str(d.kind)] = by_kind.get(str(d.kind), 0) + 1
        if d.ref not in refs:
            refs.append(d.ref)

    n_void = getattr(report, "n_void", 0) or 0
    subject = f"{routine} " if routine else ""
    if report.equivalent and not n_void:
        verdict = (
            f"EQUIVALENT — {subject}candidate matched the baseline on all "
            f"{report.n_cases} case(s): same output, same error behaviour, and "
            "identical global state after every run."
        )
    elif report.equivalent and n_void:
        verdict = (
            f"EQUIVALENT ON SCORED CASES — no divergence in "
            f"{report.n_cases - n_void} case(s), but {n_void} case(s) were void "
            "(transaction frame lost) and carry no information. Treat this as "
            "incomplete, not as a pass."
        )
    else:
        verdict = (
            f"NOT EQUIVALENT — {subject}candidate diverged on {report.n_diverged} "
            f"of {report.n_cases} case(s), with {len(divs)} divergence(s) across "
            f"{len(refs)} distinct reference(s)."
        )

    lines = [verdict]
    if divs:
        lines.append("")
        lines.append("Divergences (each names exactly what moved):")
        for e in explained:
            lines.append(e.as_text())
        if len(divs) > len(explained):
            lines.append(
                f"... and {len(divs) - len(explained)} more divergence(s) not "
                "shown; fix the ones above and re-run."
            )
        if by_kind.get("global"):
            lines.append("")
            lines.append(
                "A global divergence means the database ended up in a different "
                "state. Fixing the returned value alone is not enough."
            )
    if n_void:
        lines.append(
            f"{n_void} case(s) were void and were not scored. Re-run; if they "
            "stay void the candidate is collapsing the verifier's transaction "
            "frame (an unbalanced TCOMMIT or a bare TROLLBACK)."
        )

    return RenderedReport(
        equivalent=bool(report.equivalent),
        n_cases=report.n_cases,
        n_diverged=report.n_diverged,
        n_void=n_void,
        verdict=verdict,
        divergences=explained,
        by_kind=by_kind,
        refs_touched=refs,
        feedback="\n".join(lines),
    )


def render_exec_result(result: ExecResult) -> dict[str, Any]:
    """Serialise an :class:`ExecResult`, annotating the MUMPS error code."""
    return {
        "stdout": result.stdout,
        "error": result.error,
        "error_meaning": _describe_error(result.error or "") if result.error else "",
        "globals_out": dict(result.globals_out),
        "globals_written": sorted(result.globals_out),
        "duration_ms": result.duration_ms,
        "restarts": getattr(result, "restarts", 0),
        "void": bool(getattr(result, "void", False)),
    }
