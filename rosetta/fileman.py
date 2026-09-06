"""Minimal FileMan content editor for the Rosetta proof of concept."""

from __future__ import annotations

import re
from typing import Mapping, Protocol

_NUMBER = re.compile(r"(?:(?:0|[1-9][0-9]*)(?:\.[0-9]+)?|\.[0-9]+)")
_IEN = re.compile(r"[1-9][0-9]*")


class FileManError(RuntimeError):
    pass


class FileManRuntime(Protocol):
    def apply_fileman_record(
        self, file_number: str, iens: str, fields: dict[str, str]
    ) -> dict[str, object]: ...


def _number(value: object, label: str) -> str:
    text = str(value).strip()
    if not _NUMBER.fullmatch(text) or float(text) <= 0:
        raise FileManError(f"{label} must be a positive FileMan number")
    return text


def apply_record(
    file_number: object,
    fields: Mapping[object, object],
    runtime: FileManRuntime,
    *,
    ien: object | None = None,
) -> dict[str, object]:
    """Create or update one top-level FileMan record using external values."""
    file_id = _number(file_number, "file")
    cleaned: dict[str, str] = {}
    for field, value in fields.items():
        field_id = _number(field, "field")
        if field_id in cleaned:
            raise FileManError(f"duplicate field {field_id}")
        cleaned[field_id] = str(value)
    if not cleaned:
        raise FileManError("at least one --field FIELD VALUE is required")
    if ien is None:
        if ".01" not in cleaned:
            raise FileManError("creating a record requires field .01")
        iens = "+1,"
    else:
        ien_text = str(ien).strip()
        if not _IEN.fullmatch(ien_text):
            raise FileManError("IEN must be a positive integer")
        iens = ien_text + ","
    try:
        return runtime.apply_fileman_record(file_id, iens, cleaned)
    except Exception as exc:
        if isinstance(exc, FileManError):
            raise
        raise FileManError(str(exc)) from exc
