"""Auditable database change plans for the Rosetta editor.

The verifier answers whether two routine versions behave alike.  Intentional
maintenance needs a different primitive: describe the exact persistent state
that is allowed to move, pin the state observed while the plan was authored,
apply atomically, and retain a full-database rollback point.

This module owns that product contract and no YottaDB implementation details.
Only :mod:`rosetta.core` reads or writes globals; tests can therefore exercise
the complete planning and conflict logic with an in-memory runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

PLAN_SCHEMA = "rosetta-db-change/v1"
RECEIPT_SCHEMA = "rosetta-db-change-receipt/v1"
ROLLBACK_SCHEMA = "rosetta-db-rollback/v1"

_GLOBAL_NAME = re.compile(r"\^[A-Za-z%][A-Za-z0-9]*")
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")
_BLOCKED_ROOTS = frozenset({"^ROSLOG", "^ROSTMP", "^ROSSTATE"})


class DatabaseChangeError(RuntimeError):
    """A plan is invalid, stale, unsafe, or could not be applied cleanly."""


class DatabaseRuntime(Protocol):
    """The narrow core seam used by the editor's database workspace."""

    config: Any

    def read_globals(self, refs: Sequence[str]) -> dict[str, tuple[bool, str]]: ...

    def apply_global_changes(
        self, changes: Sequence[Mapping[str, str]]
    ) -> dict[str, tuple[bool, str]]: ...

    def snapshot(self) -> str: ...

    def restore(self, snap_id: str) -> None: ...


@dataclass(frozen=True)
class Mutation:
    """One requested leaf-node mutation before its current state is captured."""

    op: str
    ref: str
    value: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _digest(value: Mapping[str, Any]) -> str:
    body = {k: v for k, v in value.items() if k not in {"sha256", "receipt_sha256"}}
    return hashlib.sha256(_canonical(body)).hexdigest()


def validate_ref(raw: str) -> str:
    """Validate the deliberately small, injection-safe global-ref grammar.

    Rosetta's database editor operates on explicit leaf nodes.  Dynamic
    expressions, functions, variables, root-wide operations, and control
    characters are refused.  Quoted string subscripts use M's doubled-quote
    escaping; numeric subscripts use canonical decimal syntax.
    """

    ref = str(raw).strip()
    if not ref or len(ref) > 512 or any(ord(ch) < 32 for ch in ref):
        raise DatabaseChangeError("global reference is empty, too long, or contains control characters")
    match = _GLOBAL_NAME.match(ref)
    if match is None:
        raise DatabaseChangeError(f"invalid global reference {raw!r}")
    name = match.group(0).upper()
    if name in _BLOCKED_ROOTS:
        raise DatabaseChangeError(f"{name} is reserved for Rosetta internals")
    rest = ref[match.end():]
    if len(rest) < 3 or not rest.startswith("(") or not rest.endswith(")"):
        raise DatabaseChangeError(
            "database changes require an explicit leaf reference such as ^VA(4,123,0); root-wide changes are refused"
        )
    body = rest[1:-1]
    parts: list[str] = []
    current = ""
    quoted = False
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == '"':
            current += ch
            if quoted and i + 1 < len(body) and body[i + 1] == '"':
                current += '"'
                i += 2
                continue
            quoted = not quoted
        elif ch == "," and not quoted:
            parts.append(current)
            current = ""
        else:
            current += ch
        i += 1
    if quoted:
        raise DatabaseChangeError(f"unterminated quoted subscript in {raw!r}")
    parts.append(current)
    if not parts or any(not part for part in parts):
        raise DatabaseChangeError(f"empty subscript in {raw!r}")
    for part in parts:
        if _NUMBER.fullmatch(part):
            continue
        if len(part) >= 2 and part.startswith('"') and part.endswith('"'):
            inner = part[1:-1]
            if '"' in inner.replace('""', ""):
                raise DatabaseChangeError(f"invalid quote escaping in {raw!r}")
            continue
        raise DatabaseChangeError(
            f"dynamic subscript {part!r} is not allowed; use a number or quoted literal"
        )
    return name + "(" + ",".join(parts) + ")"


def _target(runtime: DatabaseRuntime) -> dict[str, str]:
    config = runtime.config
    return {
        "container": str(config.container),
        "instance": str(config.instance),
    }


def _state(present: bool, value: str = "") -> dict[str, Any]:
    return {"present": bool(present), **({"value": value} if present else {})}


def capture_plan(
    request: str,
    mutations: Iterable[Mutation],
    runtime: DatabaseRuntime,
    *,
    title: str = "",
) -> dict[str, Any]:
    """Capture a conflict-detecting change plan from live database state."""

    request = request.strip()
    if not request:
        raise DatabaseChangeError("a plain-language change request is required")
    cleaned: list[Mutation] = []
    seen: set[str] = set()
    for item in mutations:
        op = item.op.lower().strip()
        if op not in {"set", "kill"}:
            raise DatabaseChangeError(f"unsupported database operation {item.op!r}")
        ref = validate_ref(item.ref)
        if ref in seen:
            raise DatabaseChangeError(f"duplicate operation for {ref}")
        seen.add(ref)
        cleaned.append(Mutation(op, ref, str(item.value)))
    if not cleaned:
        raise DatabaseChangeError("at least one --set or --kill operation is required")

    current = runtime.read_globals([item.ref for item in cleaned])
    operations: list[dict[str, Any]] = []
    for item in cleaned:
        present, value = current[item.ref]
        after = _state(item.op == "set", item.value)
        if _state(present, value) == after:
            raise DatabaseChangeError(f"{item.ref} already has the requested state")
        operations.append(
            {
                "op": item.op,
                "ref": item.ref,
                "before": _state(present, value),
                "after": after,
            }
        )

    plan: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "change_id": "chg-" + uuid.uuid4().hex[:12],
        "created_at": _now(),
        "title": title.strip() or request[:80],
        "request": request,
        "target": _target(runtime),
        "operations": operations,
        "safety": {
            "atomic": True,
            "snapshot_before_apply": True,
            "leaf_nodes_only": True,
            "note": "Direct global edits are low-level. Use approved FileMan APIs for application records that require indexes or validation.",
        },
    }
    plan["sha256"] = _digest(plan)
    return plan


def validate_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Return a normalized plan, refusing edits or unsafe references."""

    doc = dict(plan)
    if doc.get("schema") != PLAN_SCHEMA:
        raise DatabaseChangeError(f"unsupported plan schema {doc.get('schema')!r}")
    if not isinstance(doc.get("request"), str) or not doc["request"].strip():
        raise DatabaseChangeError("plan request is missing")
    target = doc.get("target")
    if not isinstance(target, dict) or not target.get("container") or not target.get("instance"):
        raise DatabaseChangeError("plan target is missing")
    operations = doc.get("operations")
    if not isinstance(operations, list) or not operations:
        raise DatabaseChangeError("plan has no operations")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for raw in operations:
        if not isinstance(raw, dict):
            raise DatabaseChangeError("every operation must be an object")
        op = str(raw.get("op", "")).lower()
        ref = validate_ref(str(raw.get("ref", "")))
        if op not in {"set", "kill"}:
            raise DatabaseChangeError(f"unsupported operation {op!r} for {ref}")
        if ref in seen:
            raise DatabaseChangeError(f"duplicate operation for {ref}")
        seen.add(ref)
        before, after = raw.get("before"), raw.get("after")
        if not isinstance(before, dict) or not isinstance(before.get("present"), bool):
            raise DatabaseChangeError(f"{ref} has no pinned before state")
        if not isinstance(after, dict) or not isinstance(after.get("present"), bool):
            raise DatabaseChangeError(f"{ref} has no intended after state")
        if before["present"] and not isinstance(before.get("value"), str):
            raise DatabaseChangeError(f"{ref} before value must be a string")
        if after["present"] and not isinstance(after.get("value"), str):
            raise DatabaseChangeError(f"{ref} after value must be a string")
        if (op == "set") != after["present"]:
            raise DatabaseChangeError(f"{ref} operation and after state disagree")
        normalized.append({"op": op, "ref": ref, "before": before, "after": after})
    doc["operations"] = normalized
    supplied = str(doc.get("sha256", ""))
    if not supplied or supplied != _digest(doc):
        raise DatabaseChangeError("plan digest does not match; recapture the plan after editing it")
    return doc


def _assert_target(doc: Mapping[str, Any], runtime: DatabaseRuntime) -> None:
    if dict(doc["target"]) != _target(runtime):
        raise DatabaseChangeError(
            f"plan targets {doc['target']} but the active environment is {_target(runtime)}"
        )


def preview_plan(plan: Mapping[str, Any], runtime: DatabaseRuntime) -> dict[str, Any]:
    """Compare pinned, current, and intended state without writing anything."""

    doc = validate_plan(plan)
    _assert_target(doc, runtime)
    refs = [op["ref"] for op in doc["operations"]]
    current = runtime.read_globals(refs)
    rows: list[dict[str, Any]] = []
    ready = True
    for op in doc["operations"]:
        present, value = current[op["ref"]]
        state = _state(present, value)
        matches = state == op["before"]
        ready = ready and matches
        rows.append({**op, "current": state, "precondition_matches": matches})
    return {
        "change_id": doc["change_id"],
        "target": doc["target"],
        "request": doc["request"],
        "ready": ready,
        "operations": rows,
    }


def apply_plan(plan: Mapping[str, Any], runtime: DatabaseRuntime) -> dict[str, Any]:
    """Snapshot, atomically apply, and independently verify a database plan."""

    doc = validate_plan(plan)
    _assert_target(doc, runtime)
    preview = preview_plan(doc, runtime)
    if not preview["ready"]:
        conflicts = [row["ref"] for row in preview["operations"] if not row["precondition_matches"]]
        raise DatabaseChangeError(
            "database changed since this plan was captured; conflicts: " + ", ".join(conflicts)
        )

    online_snapshot = getattr(runtime, "online_snapshot", None)
    snapshot_id = online_snapshot() if callable(online_snapshot) else runtime.snapshot()
    changes = [
        {
            "op": op["op"],
            "ref": op["ref"],
            "value": str(op["after"].get("value", "")),
            "before_present": "1" if op["before"]["present"] else "0",
            "before_value": str(op["before"].get("value", "")),
        }
        for op in doc["operations"]
    ]
    try:
        runtime.apply_global_changes(changes)
        observed = runtime.read_globals([op["ref"] for op in doc["operations"]])
        mismatches = [
            op["ref"]
            for op in doc["operations"]
            if _state(*observed[op["ref"]]) != op["after"]
        ]
        if mismatches:
            raise DatabaseChangeError("post-apply verification failed for " + ", ".join(mismatches))
    except Exception as exc:
        # APPLYG is one transaction, so ordinary worker errors commit nothing.
        # A post-commit verification failure is reversed through the same
        # optimistic, atomic path. The full online snapshot remains the
        # disaster-recovery artifact when surgical reversal cannot be proven.
        inverse = [
            {
                "op": "set" if op["before"]["present"] else "kill",
                "ref": op["ref"],
                "value": str(op["before"].get("value", "")),
                "before_present": "1" if op["after"]["present"] else "0",
                "before_value": str(op["after"].get("value", "")),
            }
            for op in doc["operations"]
        ]
        try:
            current = runtime.read_globals([op["ref"] for op in doc["operations"]])
            if any(_state(*current[op["ref"]]) == op["after"] for op in doc["operations"]):
                runtime.apply_global_changes(inverse)
        except Exception as restore_exc:
            raise DatabaseChangeError(
                f"apply failed ({exc}) and automatic inverse rollback also failed ({restore_exc}); recover from {snapshot_id}"
            ) from restore_exc
        if isinstance(exc, DatabaseChangeError):
            raise
        raise DatabaseChangeError(f"apply failed; no mutation committed or the exact inverse was applied: {exc}") from exc

    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "change_id": doc["change_id"],
        "applied_at": _now(),
        "request": doc["request"],
        "target": doc["target"],
        "plan_sha256": doc["sha256"],
        "snapshot_id": snapshot_id,
        "snapshot_kind": "MUPIP DATABASE ONLINE",
        "operations": doc["operations"],
        "verified": True,
        "persistent": True,
        "rollback": "atomic inverse with pinned after-state; full snapshot retained for disaster recovery",
    }
    receipt["receipt_sha256"] = _digest(receipt)
    return receipt


def validate_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    doc = dict(receipt)
    if doc.get("schema") != RECEIPT_SCHEMA:
        raise DatabaseChangeError(f"unsupported receipt schema {doc.get('schema')!r}")
    supplied = str(doc.get("receipt_sha256", ""))
    if not supplied or supplied != _digest(doc):
        raise DatabaseChangeError("receipt digest does not match")
    if not isinstance(doc.get("snapshot_id"), str):
        raise DatabaseChangeError("receipt has no rollback snapshot")
    # Reuse the strongest operation/ref validation without changing the receipt.
    plan_shape = {
        "schema": PLAN_SCHEMA,
        "request": doc.get("request"),
        "target": doc.get("target"),
        "operations": doc.get("operations"),
    }
    plan_shape["sha256"] = _digest(plan_shape)
    validate_plan(plan_shape)
    return doc


def rollback_receipt(receipt: Mapping[str, Any], runtime: DatabaseRuntime) -> dict[str, Any]:
    """Apply the guarded inverse and verify every pinned before-value."""

    doc = validate_receipt(receipt)
    _assert_target(doc, runtime)
    inverse = [
        {
            "op": "set" if op["before"]["present"] else "kill",
            "ref": op["ref"],
            "value": str(op["before"].get("value", "")),
            "before_present": "1" if op["after"]["present"] else "0",
            "before_value": str(op["after"].get("value", "")),
        }
        for op in doc["operations"]
    ]
    try:
        runtime.apply_global_changes(inverse)
    except Exception as exc:
        raise DatabaseChangeError(
            f"atomic rollback was blocked: {exc}. The full recovery point is {doc['snapshot_id']}"
        ) from exc
    observed = runtime.read_globals([op["ref"] for op in doc["operations"]])
    mismatches = [
        op["ref"]
        for op in doc["operations"]
        if _state(*observed[op["ref"]]) != op["before"]
    ]
    if mismatches:
        raise DatabaseChangeError("inverse applied but before-state verification failed for " + ", ".join(mismatches))
    result: dict[str, Any] = {
        "schema": ROLLBACK_SCHEMA,
        "change_id": doc["change_id"],
        "rolled_back_at": _now(),
        "target": doc["target"],
        "source_receipt_sha256": doc["receipt_sha256"],
        "snapshot_id": doc["snapshot_id"],
        "mode": "atomic inverse",
        "verified": True,
    }
    result["receipt_sha256"] = _digest(result)
    return result


def load_json(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatabaseChangeError(f"could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DatabaseChangeError(f"{path} must contain one JSON object")
    return value


def artifact_dir() -> Path:
    project = Path(os.environ.get("ROSETTA_PROJECT_DIR", os.getcwd())).expanduser().resolve()
    return project / ".rosetta" / "changes"


def write_artifact(document: Mapping[str, Any], path: str | Path | None = None) -> Path:
    directory = artifact_dir()
    directory.mkdir(parents=True, exist_ok=True)
    if path is None:
        stem = str(document.get("change_id", "change"))
        suffix = "rollback" if document.get("schema") == ROLLBACK_SCHEMA else (
            "receipt" if document.get("schema") == RECEIPT_SCHEMA else "plan"
        )
        output = directory / f"{stem}.{suffix}.json"
    else:
        output = Path(path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(document), indent=2, ensure_ascii=False) + "\n"
    try:
        with output.open("x", encoding="utf-8") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise DatabaseChangeError(f"refusing to overwrite existing artifact {output}") from exc
    return output
