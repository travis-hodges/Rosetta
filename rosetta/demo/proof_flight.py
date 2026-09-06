"""A short, genuinely live proof flight for the Rosetta TUI.

The longer side-by-side demo tells the full AJETIU2 story.  This flight exists
for the interactive ``/demo`` command: it executes one real VistA MUMPS routine
twice under YottaDB, first with a bad edit and then with the corrected edit,
and prints the content-addressed receipts.  It is intentionally small enough
to finish while an audience is watching.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, TextIO

from rosetta.core.runtime import shutdown
from rosetta.tools.tools import ToolError, ToolRegistry


ROOT = Path(__file__).resolve().parents[2]
ROUTINE = "PRCHUEI"
SOURCE = ROOT / "data" / "routines" / f"{ROUTINE}.m"
ORIGINAL = 'I $E(PRCSTR)="0" Q 0'
WRONG = 'I $E(PRCSTR)\'="0" Q 0'
REPAIRED = 'I $E(PRCSTR,1)="0" Q 0'
CASE = {
    "routine": ROUTINE,
    "entry": "$$VALIDUEI",
    "args": ["ZQGGH7C1MJM3"],
    "timeout_s": 10.0,
}


def _candidate(source: str, replacement: str) -> str:
    if ORIGINAL not in source:
        raise RuntimeError(f"proof-flight anchor is missing from {SOURCE}")
    return source.replace(ORIGINAL, replacement, 1)


def _verify(registry: ToolRegistry, baseline: str, candidate: str) -> dict[str, Any]:
    return registry.call(
        "verify_change",
        {
            "routine": ROUTINE,
            "baseline_src": baseline,
            "candidate_src": candidate,
            "cases": [CASE],
        },
    )


def _short(value: object, length: int = 12) -> str:
    return str(value or "")[:length]


def _receipt(result: Mapping[str, Any]) -> Mapping[str, Any]:
    receipt = result.get("proof_receipt")
    if not isinstance(receipt, Mapping):
        raise RuntimeError("the verifier returned no proof receipt")
    if not receipt.get("live") or receipt.get("provenance") != "LIVE YottaDB":
        raise RuntimeError("the proof flight did not execute on live YottaDB")
    if not receipt.get("isolation", {}).get("restored"):
        raise RuntimeError("the proof flight cannot confirm database restoration")
    return receipt


def render(wrong: Mapping[str, Any], repaired: Mapping[str, Any], stream: TextIO = sys.stdout) -> None:
    bad_receipt = _receipt(wrong)
    good_receipt = _receipt(repaired)
    divergences = wrong.get("divergences") or []
    first = divergences[0] if divergences else {}
    if wrong.get("equivalent") or not divergences:
        raise RuntimeError("the known-bad edit was not rejected")
    if not repaired.get("equivalent") or repaired.get("n_void"):
        raise RuntimeError("the corrected edit did not earn a clean verdict")

    print("ROSETTA // LIVE PROOF FLIGHT", file=stream)
    print("MUMPS · VistA · YottaDB · executed now", file=stream)
    print("", file=stream)
    print("CHANGE REQUEST", file=stream)
    print("Make the leading-position check in $$VALIDUEI^PRCHUEI explicit without changing behavior.", file=stream)
    print("", file=stream)
    print("01  UNDERSTAND   mapped labels, calls, and persistent global access", file=stream)
    print("02  ISOLATE      opened a clean_state transaction boundary", file=stream)
    print("03  EXECUTE      replayed baseline and candidate in real YottaDB", file=stream)
    print("", file=stream)
    print("✕  DIVERGENCE CAUGHT", file=stream)
    print(f"   {first.get('ref', 'stdout')}: expected {first.get('expected')!r} · actual {first.get('actual')!r}", file=stream)
    print(f"   candidate: {WRONG}", file=stream)
    print("", file=stream)
    print("04  REPAIR       restored the equality guard and made character 1 explicit", file=stream)
    print("05  REPLAY       ran the corrected candidate from the same clean state", file=stream)
    print("", file=stream)
    print("✓  VERIFIED EQUIVALENT", file=stream)
    print(f"   candidate: {REPAIRED}", file=stream)
    print("   stdout · runtime errors · persistent global state all matched", file=stream)
    print("   rollback confirmed after both proof runs", file=stream)
    print("", file=stream)
    print("PROOF RECEIPTS", file=stream)
    print(
        f"   rejected  sha256:{_short(bad_receipt.get('receipt_sha256'))}  "
        f"{bad_receipt.get('elapsed_ms')}ms  {bad_receipt.get('artifact')}",
        file=stream,
    )
    print(
        f"   accepted  sha256:{_short(good_receipt.get('receipt_sha256'))}  "
        f"{good_receipt.get('elapsed_ms')}ms  {good_receipt.get('artifact')}",
        file=stream,
    )
    print("", file=stream)
    print("The model proposed source. The interpreter supplied the verdict.", file=stream)


def run(stream: TextIO = sys.stdout) -> int:
    baseline = SOURCE.read_text(encoding="utf-8")
    registry = ToolRegistry()
    try:
        wrong = _verify(registry, baseline, _candidate(baseline, WRONG))
        repaired = _verify(registry, baseline, _candidate(baseline, REPAIRED))
        render(wrong, repaired, stream)
    finally:
        try:
            shutdown()
        except Exception as exc:  # cleanup is reported, never allowed to rewrite a proven verdict
            print(
                f"CLEANUP WARNING: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Rosetta's short live YottaDB proof flight")
    parser.add_argument("--json", action="store_true", help="reserved for future machine output")
    args = parser.parse_args(argv)
    if args.json:
        print(json.dumps({"error": "--json is not implemented for the proof flight"}))
        return 2
    try:
        return run()
    except (OSError, RuntimeError, ValueError, ToolError) as exc:
        print(f"LIVE PROOF UNAVAILABLE: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
