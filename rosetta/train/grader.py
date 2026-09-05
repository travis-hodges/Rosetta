"""RFT grader: the verifier as a reward function.

Section 6 requires this even if the reinforcement run itself is cut, and
section 8's cut order says the same in stronger terms -- the grader is the
pitch, the run is a bonus. It is the concrete form of the claim in section 12:

    "Our verifier grades the benchmark, feeds the fine-tune, and IS the
     reward function -- so this works for JOVIAL or CMS-2, and we never need
     to see your code."

Nothing here is Rosetta-specific beyond the verifier it wraps. Hand it a
runtime for any language and the same reward function applies, which is what
makes the method corpus-agnostic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

from rosetta.core.interface import ExecSpec, VerifyReport


class Verifier(Protocol):
    """The one capability a grader needs. Injected, so this is testable
    without YottaDB and portable to any language with a runtime."""

    def verify_equivalence(
        self,
        routine: str,
        baseline_src: str,
        candidate_src: str,
        cases: Sequence[ExecSpec],
    ) -> VerifyReport: ...


@dataclass(frozen=True)
class GradeResult:
    """A reward plus the evidence for it.

    `reward` is the number RFT consumes. Everything else exists so a human can
    audit why a sample scored what it did -- section 9 requires every published
    number be traceable, and a reward is a published number.
    """

    reward: float
    passed: bool
    reason: str
    n_cases: int = 0
    n_diverged: int = 0
    n_void: int = 0
    divergences: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reward": self.reward,
            "passed": self.passed,
            "reason": self.reason,
            "n_cases": self.n_cases,
            "n_diverged": self.n_diverged,
            "n_void": self.n_void,
            "divergences": self.divergences[:10],
        }


# Rewards are deliberately explicit rather than magic numbers at call sites.
REWARD_PASS = 1.0
REWARD_FAIL = 0.0
REWARD_UNGRADABLE = 0.0


class RewardGrader:
    """Wraps a verifier as a reward function.

    Two shaping modes:

    `binary` (default) -- 1.0 iff the candidate is behaviourally equivalent to
    the baseline. This is the honest signal: the benchmark scores equivalence,
    so the reward should too, and a policy cannot farm partial credit by
    getting "closer" in a way that still ships a regression.

    `shaped` -- scales with the fraction of cases that did NOT diverge, so a
    candidate failing 1 of 10 cases outranks one failing 9. Useful early in RL
    when a binary signal is too sparse to learn from. It is strictly a training
    aid and must never be used to report a benchmark result.

    A VOID case is never scored either way. A void frame carries no information
    (the code under test collapsed the verifier's transaction), so rewarding or
    punishing it would be training on noise.
    """

    def __init__(self, verifier: Verifier, shaping: str = "binary") -> None:
        if shaping not in ("binary", "shaped"):
            raise ValueError(f"unknown shaping mode: {shaping!r}")
        self.verifier = verifier
        self.shaping = shaping

    def grade(
        self,
        routine: str,
        baseline_src: str,
        candidate_src: str,
        cases: Sequence[ExecSpec],
    ) -> GradeResult:
        if not candidate_src.strip():
            return GradeResult(REWARD_UNGRADABLE, False, "empty candidate")
        try:
            report = self.verifier.verify_equivalence(
                routine, baseline_src, candidate_src, cases
            )
        except Exception as exc:  # noqa: BLE001 -- a failed grade is a result
            # Deliberate: the grader must return a reward for every sample or
            # the RL run stalls. It is still loud -- the reason names the error.
            return GradeResult(
                REWARD_UNGRADABLE, False, f"verifier raised: {type(exc).__name__}: {exc}"
            )
        return self._from_report(report)

    def _from_report(self, report: VerifyReport) -> GradeResult:
        gradable = report.n_cases - report.n_void
        divergences = [
            f"{d.kind} {d.ref}: {d.expected!r} -> {d.actual!r}"
            for d in report.divergences
        ]
        if gradable <= 0:
            return GradeResult(
                REWARD_UNGRADABLE, False, "every case was void; nothing to score",
                report.n_cases, report.n_diverged, report.n_void, divergences,
            )
        if report.equivalent:
            return GradeResult(
                REWARD_PASS, True, "equivalent to baseline",
                report.n_cases, report.n_diverged, report.n_void, divergences,
            )
        if self.shaping == "shaped":
            clean = max(gradable - report.n_diverged, 0)
            reward = round(clean / gradable, 6)
        else:
            reward = REWARD_FAIL
        return GradeResult(
            reward, False,
            f"{report.n_diverged} of {gradable} gradable cases diverged",
            report.n_cases, report.n_diverged, report.n_void, divergences,
        )


def grade_jsonl(
    grader: RewardGrader, path: Path, out: Path | None = None
) -> list[GradeResult]:
    """Grade a JSONL file of samples.

    Each line needs `routine`, `baseline_src`, `candidate_src` and `cases`,
    where each case is a dict accepted by ExecSpec.
    """
    results: list[GradeResult] = []
    sink = out.open("w") if out else None
    try:
        with path.open() as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                sample = json.loads(line)
                missing = [
                    k for k in ("routine", "baseline_src", "candidate_src")
                    if k not in sample
                ]
                if missing:
                    raise ValueError(f"{path}:{lineno} missing {missing}")
                cases = [ExecSpec(**c) for c in sample.get("cases", [])]
                result = grader.grade(
                    sample["routine"], sample["baseline_src"],
                    sample["candidate_src"], cases,
                )
                results.append(result)
                if sink:
                    sink.write(json.dumps(result.to_dict()) + "\n")
    finally:
        if sink:
            sink.close()
    return results


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Grade candidates with the verifier.")
    ap.add_argument("samples", type=Path, help="JSONL of samples to grade")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--shaping", default="binary", choices=["binary", "shaped"])
    args = ap.parse_args(argv)

    from rosetta.core.runtime import Runtime  # imported late: needs a container

    grader = RewardGrader(Runtime(), shaping=args.shaping)
    results = grade_jsonl(grader, args.samples, args.out)
    if results:
        mean = sum(r.reward for r in results) / len(results)
        print(f"{len(results)} samples  mean reward {mean:.4f}  "
              f"passed {sum(r.passed for r in results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
