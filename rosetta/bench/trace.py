"""The benchmark trace record: one JSON object per model attempt.

Section 9 of docs/PROJECT.md requires full traces so that every published number
is auditable. Benchmark traces live in ``results/bench/*.jsonl`` and this module
is the **schema of record** for them. The model-running harness produces them;
:mod:`rosetta.bench.score` and :mod:`rosetta.bench.report` consume them, and no
published figure may come from anywhere else.

One line of JSON = one *attempt*: one model, one condition, one task, one
independent sample. A task scored with three attempts produces three lines.

Design rules, in order of importance:

1. **The verdict is not the model's opinion.** ``verdict`` is the grading
   ``VerifyReport`` produced by ``rosetta.core.verify_equivalence`` after the
   attempt finished, against the task's frozen case suite. A record whose
   ``verdict`` is absent is *ungraded* and is never scored.
2. **Assertion of correctness is recorded as an act, not inferred from prose.**
   See :class:`Assertion`. Nothing in this project regex-matches "I'm confident"
   out of model text; section 9 forbids an LLM judge, and that prohibition
   applies to judging the model's confidence too.
3. **The reader tolerates unknown fields.** Anything the producer adds that is
   not in this schema is preserved verbatim in :attr:`TraceRecord.extra` rather
   than dropped or rejected, so the producer can carry extra evidence without a
   lockstep change here. Missing *required* fields, by contrast, raise.

Producing a record::

    from rosetta.bench.trace import TraceRecord, Assertion, Verdict, write_traces

    rec = TraceRecord(
        run_id="2026-09-05T14:00Z-gpt", condition="scaffolded",
        task_id="PRCHUEI-CMP_FLIP-L12-ab12cd34", routine="PRCHUEI",
        operator="CMP_FLIP", difficulty="easy", attempt=1, model="some-model",
        submitted=True, candidate_src="PRCHUEI ;...",
        assertion=Assertion(claimed_correct=True, source="explicit"),
        tool_calls=[ToolCall(name="verify_change", equivalent=False)],
        verdict=Verdict.from_report(report),
    )
    write_traces([rec], "results/bench/scaffolded.jsonl")
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal, Mapping, Sequence

__all__ = [
    "SCHEMA",
    "PROTOCOL",
    "CONDITIONS",
    "VERIFIER_TOOLS",
    "Assertion",
    "AssertionSource",
    "Condition",
    "DivergenceRecord",
    "ToolCall",
    "TraceFormatError",
    "TraceRecord",
    "Verdict",
    "iter_traces",
    "read_traces",
    "trace_paths",
    "task_fingerprint",
    "write_traces",
]

SCHEMA = "rosetta.bench.trace/1"
PROTOCOL = "rosetta.bench.isolated-harness-feedback/2"


def task_fingerprint(task: Mapping[str, Any]) -> str:
    """Bind a trace to the exact reference, mutant, routine, and grading cases."""
    payload = {key: task[key] for key in (
        "task_id", "routine", "baseline_src", "mutated_src", "cases"
    )}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()

Condition = Literal["baseline", "scaffolded", "tuned_scaffolded"]

#: The three conditions of section 9. The website publishes ids from this set.
CONDITIONS: tuple[str, ...] = ("baseline", "scaffolded", "tuned_scaffolded")

#: Tool names that constitute a *verifier call* for the repair-iteration metric.
#: These are the two tools in section 6's table that execute and compare.
VERIFIER_TOOLS: frozenset[str] = frozenset({"verify_change", "run_task_cases"})

AssertionSource = Literal["explicit", "submission", "declined", "unknown"]


class TraceFormatError(ValueError):
    """A trace line is missing a required field or has the wrong type.

    Raised rather than skipped. A silently dropped trace line is a silently
    wrong benchmark number.
    """


def _require(obj: Mapping[str, Any], key: str, types: tuple[type, ...], where: str) -> Any:
    if key not in obj:
        raise TraceFormatError(f"{where}: missing required field {key!r}")
    value = obj[key]
    if not isinstance(value, types) or isinstance(value, bool) and bool not in types:
        raise TraceFormatError(
            f"{where}: field {key!r} must be "
            f"{' or '.join(t.__name__ for t in types)}, got {type(value).__name__}"
        )
    return value


def _known(cls_fields: Iterable[str], obj: Mapping[str, Any]) -> dict[str, Any]:
    """Unknown keys, preserved so a richer producer loses nothing."""
    known = set(cls_fields)
    return {k: v for k, v in obj.items() if k not in known}


@dataclass(frozen=True)
class DivergenceRecord:
    """One divergence from the grading report, flattened for JSONL.

    Mirrors ``rosetta.core.interface.Divergence``. ``expected``/``actual`` may be
    truncated by the producer -- they exist so a reader can see *what* moved, not
    to reproduce the value.
    """

    kind: str
    ref: str
    expected: str = ""
    actual: str = ""
    case_index: int = 0

    @classmethod
    def from_json(cls, obj: Mapping[str, Any], where: str = "divergence") -> DivergenceRecord:
        return cls(
            kind=str(_require(obj, "kind", (str,), where)),
            ref=str(_require(obj, "ref", (str,), where)),
            expected=str(obj.get("expected", "")),
            actual=str(obj.get("actual", "")),
            case_index=int(obj.get("case_index", 0)),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ref": self.ref,
            "expected": self.expected,
            "actual": self.actual,
            "case_index": self.case_index,
        }


@dataclass(frozen=True)
class Verdict:
    """The grading result: ``verify_equivalence`` run *by the harness*, after
    the attempt, over the task's frozen case suite.

    ``equivalent=True`` is the only thing that counts as a pass. The model's own
    ``verify_change`` calls during the attempt are tool calls, not verdicts --
    they are recorded in :attr:`TraceRecord.tool_calls` and a disagreement
    between them and this verdict is itself a reported number.
    """

    equivalent: bool
    n_cases: int
    n_diverged: int
    n_void: int = 0
    divergences: tuple[DivergenceRecord, ...] = ()

    @property
    def trustworthy(self) -> bool:
        """False when any case is void, or none ran.

        A void case "carries NO information and must never be scored" (frozen
        contract). A partially executed suite must not share a denominator with a full suite.
        """
        return self.n_cases > 0 and self.n_void == 0

    @classmethod
    def from_report(cls, report: Any, max_divergences: int = 20) -> Verdict:
        """Build from a ``rosetta.core.interface.VerifyReport``."""
        divs = tuple(
            DivergenceRecord(
                kind=str(d.kind),
                ref=str(d.ref),
                expected=str(d.expected),
                actual=str(d.actual),
                case_index=int(d.case_index),
            )
            for d in list(report.divergences)[:max_divergences]
        )
        return cls(
            equivalent=bool(report.equivalent),
            n_cases=int(report.n_cases),
            n_diverged=int(report.n_diverged),
            n_void=int(getattr(report, "n_void", 0)),
            divergences=divs,
        )

    @classmethod
    def from_json(cls, obj: Mapping[str, Any], where: str = "verdict") -> Verdict:
        return cls(
            equivalent=bool(_require(obj, "equivalent", (bool,), where)),
            n_cases=int(_require(obj, "n_cases", (int,), where)),
            n_diverged=int(_require(obj, "n_diverged", (int,), where)),
            n_void=int(obj.get("n_void", 0)),
            divergences=tuple(
                DivergenceRecord.from_json(d, where) for d in obj.get("divergences", [])
            ),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "equivalent": self.equivalent,
            "n_cases": self.n_cases,
            "n_diverged": self.n_diverged,
            "n_void": self.n_void,
            "divergences": [d.to_json() for d in self.divergences],
        }


@dataclass(frozen=True)
class ToolCall:
    """One tool invocation during an attempt, in order.

    ``equivalent`` is the verdict the *tool* returned, for tools in
    :data:`VERIFIER_TOOLS`; ``None`` for every other tool.
    """

    name: str
    ok: bool = True
    equivalent: bool | None = None
    at: str | None = None
    detail: str = ""

    @property
    def is_verifier_call(self) -> bool:
        return self.name in VERIFIER_TOOLS

    @classmethod
    def from_json(cls, obj: Mapping[str, Any], where: str = "tool_call") -> ToolCall:
        equivalent = obj.get("equivalent")
        return cls(
            name=str(_require(obj, "name", (str,), where)),
            ok=bool(obj.get("ok", True)),
            equivalent=None if equivalent is None else bool(equivalent),
            at=None if obj.get("at") is None else str(obj["at"]),
            detail=str(obj.get("detail", "")),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "equivalent": self.equivalent,
            "at": self.at,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Assertion:
    """Whether the model asserted its answer was correct.

    This drives the false-confidence rate -- section 9's "most important number
    in the project" -- so what counts is defined here, once, mechanically.

    ``claimed_correct``
        ``True``  the model explicitly claimed the fix is correct.
        ``False`` the model explicitly declined to claim correctness (hedged,
                  gave up, or said it could not verify).
        ``None``  the harness recorded no explicit claim either way. The reader
                  then falls back to the submission rule below.

    ``source``
        ``"explicit"``   the harness captured a structured claim (e.g. the
                         model set ``correct=true`` on the submit tool).
        ``"submission"`` no structured claim; the value was derived from the
                         fact that a final candidate was submitted.
        ``"declined"``   the attempt ended with no candidate offered.
        ``"unknown"``    producer did not say.

    **The submission rule.** When ``claimed_correct is None`` and the attempt
    submitted a final candidate as its answer, the reader treats that as an
    assertion of correctness. Handing over a patch *as the answer* is a claim
    that it is correct; a program office receiving it would act on it. The
    alternative -- reading confidence out of prose -- would put an LLM judge on
    the critical path of the headline number, which section 9 rules out.

    An attempt that submits nothing is **not** an assertion. It still fails
    pass@1; it just is not *confidently* wrong, and conflating the two would
    inflate the number we most need to be trusted.
    """

    claimed_correct: bool | None = None
    source: AssertionSource = "unknown"
    evidence: str = ""

    @classmethod
    def from_json(cls, obj: Mapping[str, Any], where: str = "assertion") -> Assertion:
        claimed = obj.get("claimed_correct")
        source = str(obj.get("source", "unknown"))
        return cls(
            claimed_correct=None if claimed is None else bool(claimed),
            source=source,  # type: ignore[arg-type]
            evidence=str(obj.get("evidence", "")),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "claimed_correct": self.claimed_correct,
            "source": self.source,
            "evidence": self.evidence,
        }


_REQUIRED = ("condition", "task_id", "attempt")


@dataclass(frozen=True)
class TraceRecord:
    """One attempt by one model on one task under one condition.

    Required on the wire: ``condition``, ``task_id``, ``attempt``. Everything
    else has a defined default, but a record without a ``verdict`` is ungraded
    and :mod:`rosetta.bench.score` will exclude it and say so.
    """

    condition: str
    task_id: str
    attempt: int = 1

    run_id: str = ""
    routine: str = ""
    operator: str = ""
    difficulty: str = ""
    model: str = ""
    seed: int | None = None

    started_at: str | None = None
    finished_at: str | None = None

    submitted: bool = False
    candidate_src: str | None = None

    assertion: Assertion = field(default_factory=Assertion)
    tool_calls: tuple[ToolCall, ...] = ()
    #: Producer may state the verifier-call count directly; otherwise it is
    #: counted from ``tool_calls``.
    verifier_calls: int | None = None

    verdict: Verdict | None = None
    #: Set when the *harness* failed (crash, timeout, API error). Such a record
    #: is excluded from scoring and counted separately -- never a failure.
    harness_error: str | None = None

    #: Anything the producer emitted that this schema does not name.
    extra: dict[str, Any] = field(default_factory=dict)

    # -- derived -------------------------------------------------------

    @property
    def verifier_call_count(self) -> int:
        """Verifier tool calls made during this attempt."""
        if self.verifier_calls is not None:
            return int(self.verifier_calls)
        return sum(1 for c in self.tool_calls if c.is_verifier_call)

    @property
    def graded(self) -> bool:
        return self.verdict is not None

    @property
    def scoreable(self) -> bool:
        """True when this attempt can carry a number.

        Excluded: harness failures (we measured our own bug, not the model),
        ungraded attempts, and verdicts with no trustworthy case.
        """
        return (
            self.harness_error is None
            and self.verdict is not None
            and self.verdict.trustworthy
        )

    @property
    def exclusion_reason(self) -> str | None:
        if self.harness_error is not None:
            return "harness_error"
        if self.verdict is None:
            return "ungraded"
        if not self.verdict.trustworthy:
            return "void"
        return None

    @property
    def passed(self) -> bool:
        """The grading verifier said equivalent. The only definition of a pass."""
        return bool(self.verdict and self.verdict.equivalent)

    @property
    def asserted_correct(self) -> bool:
        """Did the model assert this answer was correct? See :class:`Assertion`."""
        if self.assertion.claimed_correct is not None:
            return self.assertion.claimed_correct
        return bool(self.submitted)

    @property
    def tool_said_equivalent(self) -> bool:
        """The last verifier tool call in the attempt reported equivalence."""
        for call in reversed(self.tool_calls):
            if call.is_verifier_call and call.equivalent is not None:
                return bool(call.equivalent)
        return False

    # -- serialisation -------------------------------------------------

    @classmethod
    def from_json(cls, obj: Mapping[str, Any], where: str = "trace") -> TraceRecord:
        if not isinstance(obj, Mapping):
            raise TraceFormatError(f"{where}: expected a JSON object")
        for key in _REQUIRED:
            if key not in obj:
                hint = ""
                if "kind" in obj or "seq" in obj:
                    hint = (
                        " -- this looks like a demo event stream rather than a "
                        "benchmark trace; benchmark traces live in results/bench/"
                    )
                raise TraceFormatError(
                    f"{where}: missing required field {key!r}{hint}"
                )
        condition = str(obj["condition"])
        if condition not in CONDITIONS:
            raise TraceFormatError(
                f"{where}: condition {condition!r} is not one of {CONDITIONS}"
            )
        attempt = obj["attempt"]
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
            raise TraceFormatError(f"{where}: attempt must be an integer >= 1")

        verdict_obj = obj.get("verdict")
        seed = obj.get("seed")
        names = {f for f in cls.__dataclass_fields__} | {"schema"}
        return cls(
            condition=condition,
            task_id=str(obj["task_id"]),
            attempt=attempt,
            run_id=str(obj.get("run_id", "")),
            routine=str(obj.get("routine", "")),
            operator=str(obj.get("operator", "")),
            difficulty=str(obj.get("difficulty", "")),
            model=str(obj.get("model", "")),
            seed=None if seed is None else int(seed),
            started_at=None if obj.get("started_at") is None else str(obj["started_at"]),
            finished_at=None if obj.get("finished_at") is None else str(obj["finished_at"]),
            submitted=bool(obj.get("submitted", obj.get("candidate_src") is not None)),
            candidate_src=(
                None if obj.get("candidate_src") is None else str(obj["candidate_src"])
            ),
            assertion=Assertion.from_json(obj.get("assertion") or {}, where),
            tool_calls=tuple(
                ToolCall.from_json(c, where) for c in obj.get("tool_calls", [])
            ),
            verifier_calls=(
                None if obj.get("verifier_calls") is None else int(obj["verifier_calls"])
            ),
            verdict=None if verdict_obj is None else Verdict.from_json(verdict_obj, where),
            harness_error=(
                None if obj.get("harness_error") is None else str(obj["harness_error"])
            ),
            extra=_known(names, obj),
        )

    def to_json(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "schema": SCHEMA,
            "run_id": self.run_id,
            "condition": self.condition,
            "task_id": self.task_id,
            "routine": self.routine,
            "operator": self.operator,
            "difficulty": self.difficulty,
            "attempt": self.attempt,
            "model": self.model,
            "seed": self.seed,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "submitted": self.submitted,
            "candidate_src": self.candidate_src,
            "assertion": self.assertion.to_json(),
            "tool_calls": [c.to_json() for c in self.tool_calls],
            "verifier_calls": self.verifier_calls,
            "verdict": None if self.verdict is None else self.verdict.to_json(),
            "harness_error": self.harness_error,
        }
        for key, value in self.extra.items():
            doc.setdefault(key, value)
        return doc

    def with_task_metadata(self, routine: str, operator: str, difficulty: str) -> TraceRecord:
        """Fill in task facts the producer may have left blank."""
        return replace(
            self,
            routine=self.routine or routine,
            operator=self.operator or operator,
            difficulty=self.difficulty or difficulty,
        )


# ----------------------------------------------------------------------
# Reading and writing
# ----------------------------------------------------------------------


def iter_traces(path: str | Path) -> Iterator[TraceRecord]:
    """Yield records from one ``.jsonl`` file. Blank lines are skipped."""
    p = Path(path)
    with p.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            where = f"{p}:{lineno}"
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TraceFormatError(f"{where}: not valid JSON: {exc}") from exc
            yield TraceRecord.from_json(obj, where)


def trace_paths(source: str | Path) -> list[Path]:
    """Resolve a file or a directory of ``*.jsonl`` into a sorted file list."""
    p = Path(source)
    if p.is_dir():
        return sorted(p.glob("*.jsonl"))
    if p.is_file():
        return [p]
    raise FileNotFoundError(f"no trace file or directory at {p}")


def read_traces(source: str | Path | Sequence[str | Path]) -> list[TraceRecord]:
    """Read every record under a file, a directory, or a list of either."""
    sources: Sequence[str | Path]
    sources = [source] if isinstance(source, (str, Path)) else list(source)
    out: list[TraceRecord] = []
    for s in sources:
        for path in trace_paths(s):
            out.extend(iter_traces(path))
    return out


def write_traces(records: Iterable[TraceRecord], path: str | Path, *, append: bool = False) -> Path:
    """Write JSONL; append permits durable progress after each completed attempt."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a" if append else "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec.to_json(), ensure_ascii=False) + "\n")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m rosetta.bench.trace <file-or-dir>`` -- validate and summarise.

    The producer's self-check: it parses every record against this schema and
    reports what the scorer will and will not be able to use.
    """
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="rosetta.bench.trace", description=__doc__)
    ap.add_argument("source", help="a .jsonl file or a directory of them")
    args = ap.parse_args(argv)

    try:
        records = read_traces(args.source)
    except (TraceFormatError, FileNotFoundError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1

    by_condition: dict[str, int] = {}
    excluded: dict[str, int] = {}
    asserted = passed = 0
    for rec in records:
        by_condition[rec.condition] = by_condition.get(rec.condition, 0) + 1
        reason = rec.exclusion_reason
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
        asserted += int(rec.asserted_correct)
        passed += int(rec.passed)
    print(f"{len(records)} records, schema {SCHEMA}")
    print(f"  by condition {dict(sorted(by_condition.items()))}")
    print(f"  tasks {len({r.task_id for r in records})}")
    print(f"  asserted correct {asserted}   verified equivalent {passed}")
    print(f"  not scoreable {dict(sorted(excluded.items())) or '{}'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
