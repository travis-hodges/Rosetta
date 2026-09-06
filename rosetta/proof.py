"""Content-addressed receipts for one Rosetta verification decision.

The frozen core contract answers the correctness question.  This module does
not reinterpret that answer; it packages the already-rendered result with the
minimum provenance a human needs to distinguish a live YottaDB proof from a
mock, replay, or model assertion.

Receipts contain source hashes rather than source.  That keeps the artifact
small and safe to show on a projector while still binding it to the exact
baseline and candidate that were executed.  A digest over the canonical JSON
makes accidental or deliberate edits detectable.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "rosetta-proof/v1"

__all__ = ["SCHEMA", "build_receipt", "persist_receipt", "trace_digest"]


def _sha256(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def build_receipt(
    *,
    routine: str,
    baseline_src: str,
    candidate_src: str,
    result: Mapping[str, Any],
    backend: str,
    cases_origin: str,
    elapsed_ms: int,
) -> dict[str, Any]:
    """Bind a rendered verifier result to its inputs and execution context."""

    live = backend == "core"
    restored = live and int(result.get("n_void", 0) or 0) == 0
    body: dict[str, Any] = {
        "schema": SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provenance": "LIVE YottaDB" if live else f"{backend.upper()} BACKEND",
        "live": live,
        "runtime": "YottaDB" if live else backend,
        "routine": routine.upper(),
        "baseline_sha256": _sha256(baseline_src),
        "candidate_sha256": _sha256(candidate_src),
        "cases_origin": cases_origin,
        "n_cases": int(result.get("n_cases", 0) or 0),
        "n_diverged": int(result.get("n_diverged", 0) or 0),
        "n_void": int(result.get("n_void", 0) or 0),
        "equivalent": bool(result.get("equivalent")),
        "observables_checked": ["stdout", "runtime errors", "persistent global state"],
        "divergence_kinds": dict(result.get("by_kind", {})),
        "references_moved": list(result.get("refs_touched", [])),
        "isolation": {
            "mechanism": "clean_state transaction frame per case",
            "restored": restored,
            "detail": (
                "Every baseline and candidate execution completed inside the "
                "verifier isolation boundary; global writes were rolled back."
                if restored
                else "State restoration is not asserted for this backend or a void case."
            ),
        },
        "elapsed_ms": max(0, int(elapsed_ms)),
    }
    body["receipt_sha256"] = _sha256(_canonical(body))
    return body


def _proof_dir() -> Path:
    explicit = os.environ.get("ROSETTA_PROOF_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()
    project = Path(os.environ.get("ROSETTA_PROJECT_DIR", os.getcwd()))
    return project.expanduser().resolve() / ".rosetta" / "proofs"


def persist_receipt(receipt: Mapping[str, Any]) -> str:
    """Write a live receipt once and return its absolute path.

    The digest is part of the filename and the file is opened exclusively.  A
    second identical receipt may reuse the first artifact; it is never
    overwritten.
    """

    directory = _proof_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = str(receipt.get("created_at", "proof")).replace(":", "-")
    stamp = stamp.replace("+00-00", "Z")
    routine = "".join(ch for ch in str(receipt.get("routine", "MUMPS")) if ch.isalnum() or ch == "%")
    digest = str(receipt.get("receipt_sha256", "unknown"))[:12]
    path = directory / f"{stamp}-{routine}-{digest}.json"
    payload = json.dumps(dict(receipt), indent=2, ensure_ascii=False) + "\n"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(payload)
    except FileExistsError:
        pass
    return str(path)


def trace_digest(path: str | Path) -> str:
    """Digest an exact JSONL audit trace without parsing or normalising it."""

    return _sha256(Path(path).read_bytes())
