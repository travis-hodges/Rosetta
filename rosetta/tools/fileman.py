"""FileMan data-dictionary lookup — what a MUMPS global actually *means*.

Background for anyone who has not seen VistA. MUMPS has no tables. The
database is a set of sparse persistent arrays called **globals**, written with a
leading caret: ``^DPT(3,0)``. There is no schema in the language. VistA layers
one on top, called **FileMan**, and stores it in globals too:

* ``^DIC(<file>,0,"GL")`` — the global root that holds file ``<file>``.
  File #2 (PATIENT) lives at ``^DPT(``.
* ``^DD(<file>,<field>,0)`` — one field: ``NAME^TYPE^CODES^NODE;PIECE``.
  Field .02 of file 2 is ``SEX^RSa^M:MALE;F:FEMALE;^0;2``.

Put together, that says: ``^DPT(3,0)`` is patient record 3, and the ``^``-
delimited pieces of the string stored there are NAME, SEX, DATE OF BIRTH, ...
That translation is the whole job of this module.

Data source: ``data/fileman/dd.json.gz``, extracted once at build time by
``rosetta.tools.ddcache``. This module is pure stdlib and never touches the
container, so it works from a cold start with no network.
"""

from __future__ import annotations

import gzip
import json
import os
import re
from dataclasses import asdict, dataclass, field as dc_field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "FileSpec",
    "FieldSpec",
    "PieceValue",
    "SampleValue",
    "GlobalResolution",
    "DataDictionary",
    "default_dictionary",
    "parse_ref",
    "fileman_date",
]

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_PATH = _REPO_ROOT / "data" / "fileman" / "dd.json.gz"

#: ``^DPT(3,0)`` -> name ``^DPT``, subscripts ``["3", "0"]``.
_REF_RE = re.compile(r"^\s*(\^?%?[A-Za-z][A-Za-z0-9]*)\s*(?:\((.*)\))?\s*$", re.DOTALL)


# --------------------------------------------------------------------------
# Reference parsing
# --------------------------------------------------------------------------


def parse_ref(ref: str) -> tuple[str, list[str]]:
    """Split a global reference into its name and subscript list.

    ``"^DPT(3,0)"`` -> ``("^DPT", ["3", "0"])``. A bare ``"^DPT"`` or ``"DPT"``
    yields no subscripts. Raises ``ValueError`` on anything that is not a
    syntactically valid M global reference.
    """
    text = (ref or "").strip()
    if not text:
        raise ValueError("empty global reference")
    m = _REF_RE.match(text)
    if not m:
        raise ValueError(f"not a global reference: {ref!r}")
    name = m.group(1)
    if not name.startswith("^"):
        name = "^" + name
    subs_text = m.group(2)
    if subs_text is None or not subs_text.strip():
        return name, []
    subs: list[str] = []
    depth = 0
    in_str = False
    cur: list[str] = []
    for ch in subs_text:
        if ch == '"':
            in_str = not in_str
            cur.append(ch)
        elif in_str:
            cur.append(ch)
        elif ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            subs.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    subs.append("".join(cur).strip())
    return name, [s.strip('"') for s in subs]


def fileman_date(value: str) -> str | None:
    """Render a FileMan internal date as ISO-8601, or ``None`` if it is not one.

    FileMan stores dates as ``YYYMMDD`` where ``YYY`` is the year minus 1700,
    optionally followed by ``.HHMMSS``. ``2350407`` is 1935-04-07. This trips
    up every reader seeing VistA data for the first time.
    """
    m = re.fullmatch(r"(\d{7})(?:\.(\d{1,6}))?", value.strip())
    if not m:
        return None
    digits = m.group(1)
    year = 1700 + int(digits[:3])
    month, day = int(digits[3:5]), int(digits[5:7])
    if not (1 <= month <= 12 and 0 <= day <= 31):
        return None
    out = f"{year:04d}-{month:02d}-{day:02d}" if day else f"{year:04d}-{month:02d}"
    frac = m.group(2)
    if frac:
        frac = frac.ljust(6, "0")
        out += f"T{frac[0:2]}:{frac[2:4]}:{frac[4:6]}"
    return out


# --------------------------------------------------------------------------
# Dictionary records
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FileSpec:
    """One FileMan file (roughly: one table)."""

    number: str
    name: str
    root: str
    parent: str = ""  # populated for a subfile ("multiple")

    @property
    def is_subfile(self) -> bool:
        return bool(self.parent) and not self.root


@dataclass(frozen=True)
class FieldSpec:
    """One FileMan field, including where its value physically lives.

    ``node`` and ``piece`` are the important part: field .02 of file 2 has
    ``node="0"`` and ``piece=2``, meaning "the 2nd ``^``-delimited piece of the
    string stored at ``^DPT(<ien>,0)``".
    """

    number: str
    name: str
    kind: str                      # free text / numeric / date / set of codes / ...
    node: str
    piece: int | None
    required: bool = False
    type_raw: str = ""
    codes_raw: str = ""
    subfile: str = ""              # file number of the nested multiple, if any
    pointer_file: str = ""         # file number this field points at, if any
    pointer_root: str = ""
    set_of_codes: dict[str, str] = dc_field(default_factory=dict)


@dataclass(frozen=True)
class PieceValue:
    """One ``^``-delimited piece of a stored node, named and decoded."""

    piece: int
    raw: str
    field_number: str = ""
    field_name: str = ""
    decoded: str = ""


@dataclass(frozen=True)
class SampleValue:
    """A real node from the vehu container, with its pieces named."""

    ref: str
    value: str
    pieces: list[PieceValue] = dc_field(default_factory=list)


@dataclass(frozen=True)
class GlobalResolution:
    """Everything known about one global reference."""

    ref: str
    global_name: str
    subscripts: list[str]
    fileman_file: FileSpec | None
    matched_root: str
    record_ien: str
    node: str
    node_layout: list[FieldSpec]
    schema: list[FieldSpec]
    sample_values: list[SampleValue]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# Type decoding
# --------------------------------------------------------------------------

_KIND_BY_LETTER = {
    "F": "free text",
    "N": "numeric",
    "D": "date/time",
    "S": "set of codes",
    "P": "pointer",
    "V": "variable pointer",
    "C": "computed",
    "W": "word processing",
    "K": "MUMPS code",
    "M": "multiline",
}


def _decode_type(type_raw: str, codes_raw: str) -> dict[str, Any]:
    t = (type_raw or "").strip()
    out: dict[str, Any] = {
        "kind": "unknown",
        "required": t.startswith("R"),
        "subfile": "",
        "pointer_file": "",
        "pointer_root": "",
        "set_of_codes": {},
    }
    body = t[1:] if t.startswith("R") else t
    sub = re.match(r"^(\d+(?:\.\d+)?)", body)
    if sub:
        out["kind"] = "multiple"
        out["subfile"] = sub.group(1)
        return out
    ptr = re.search(r"P(\d+(?:\.\d+)?)", body)
    if ptr:
        out["kind"] = "pointer"
        out["pointer_file"] = ptr.group(1)
        # FileMan stores the pointed-to root without its caret ("DIC(11,").
        root = codes_raw.strip()
        if root and not root.startswith("^"):
            root = "^" + root
        if re.fullmatch(r"\^%?[A-Za-z][A-Za-z0-9]*\((?:[^()]*,)?", root or ""):
            out["pointer_root"] = root
        return out
    for ch in body:
        if ch in _KIND_BY_LETTER:
            out["kind"] = _KIND_BY_LETTER[ch]
            break
    if out["kind"] == "set of codes":
        for pair in codes_raw.split(";"):
            if ":" in pair:
                k, _, v = pair.partition(":")
                if k.strip():
                    out["set_of_codes"][k.strip()] = v.strip()
    return out


def _field_from_record(number: str, rec: list[str]) -> FieldSpec:
    name, type_raw, codes_raw, storage = (list(rec) + ["", "", "", ""])[:4]
    node, _, piece_text = storage.partition(";")
    piece = int(piece_text) if piece_text.isdigit() else None
    dec = _decode_type(type_raw, codes_raw)
    return FieldSpec(
        number=number,
        name=name,
        kind=dec["kind"],
        node=node,
        piece=piece,
        required=dec["required"],
        type_raw=type_raw,
        codes_raw=codes_raw,
        subfile=dec["subfile"],
        pointer_file=dec["pointer_file"],
        pointer_root=dec["pointer_root"],
        set_of_codes=dec["set_of_codes"],
    )


def _field_sort_key(f: FieldSpec) -> tuple[float, str]:
    try:
        return (float(f.number), "")
    except ValueError:
        return (float("inf"), f.number)


# --------------------------------------------------------------------------
# The dictionary
# --------------------------------------------------------------------------


class DataDictionary:
    """Read-only view over the extracted FileMan dictionary.

    Construct via :func:`default_dictionary` unless you are pointing at a
    custom cache in a test.
    """

    def __init__(self, cache: dict[str, Any]) -> None:
        self._raw = cache
        self._files_raw: dict[str, dict[str, Any]] = cache.get("files", {})
        self._samples: dict[str, list[list[str]]] = cache.get("samples", {})
        self._roots: dict[str, str] = {}
        for num, rec in self._files_raw.items():
            root = (rec.get("root") or "").strip()
            if root:
                self._roots.setdefault(root, num)
        # Longest root first so ^VA(200, beats ^VA( on a ^VA(200,3,0) lookup.
        self._roots_by_length = sorted(self._roots, key=len, reverse=True)

    # -- construction ------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> "DataDictionary":
        """Load the gzipped cache. Raises ``FileNotFoundError`` if absent."""
        p = path or DEFAULT_CACHE_PATH
        if not p.exists():
            raise FileNotFoundError(
                f"FileMan dictionary cache not found at {p}. "
                "Rebuild it with: python -m rosetta.tools.ddcache --build"
            )
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            return cls(json.load(fh))

    @classmethod
    def empty(cls) -> "DataDictionary":
        """A dictionary with no content, for environments without the cache."""
        return cls({"files": {}, "samples": {}, "counts": {}})

    # -- accessors ---------------------------------------------------------

    @property
    def counts(self) -> dict[str, Any]:
        return dict(self._raw.get("counts", {}))

    @property
    def available(self) -> bool:
        return bool(self._files_raw)

    def file(self, number: str) -> FileSpec | None:
        rec = self._files_raw.get(str(number))
        if rec is None:
            return None
        return FileSpec(
            number=str(number),
            name=rec.get("name", ""),
            root=rec.get("root", ""),
            parent=rec.get("up", ""),
        )

    def fields(self, number: str) -> list[FieldSpec]:
        rec = self._files_raw.get(str(number))
        if rec is None:
            return []
        out = [_field_from_record(k, v) for k, v in rec.get("fields", {}).items()]
        out.sort(key=_field_sort_key)
        return out

    def fields_on_node(self, number: str, node: str) -> list[FieldSpec]:
        """Fields physically stored at ``node``, ordered by piece position."""
        out = [
            f for f in self.fields(number)
            if f.node == node and f.piece is not None
        ]
        out.sort(key=lambda f: f.piece or 0)
        return out

    def match_root(self, ref: str) -> tuple[str, str] | None:
        """Longest global root that prefixes ``ref``; returns ``(root, file#)``."""
        for root in self._roots_by_length:
            if ref.startswith(root):
                return root, self._roots[root]
        return None

    def samples_for_root(self, root: str, limit: int = 8) -> list[tuple[str, str]]:
        return [(r, v) for r, v in self._samples.get(root, [])][:limit]

    def sample_exact(self, ref: str) -> str | None:
        root_match = self.match_root(ref)
        if not root_match:
            return None
        for r, v in self._samples.get(root_match[0], []):
            if r == ref:
                return v
        return None

    # -- the interesting part ---------------------------------------------

    def describe_value(
        self, file_number: str, node: str, value: str
    ) -> list[PieceValue]:
        """Split a stored node into named, decoded pieces.

        This is what turns ``"EIGHT,PATIENT^M^2350407^..."`` into
        ``NAME=EIGHT,PATIENT``, ``SEX=M (MALE)``, ``DATE OF BIRTH=1935-04-07``.
        """
        by_piece = {f.piece: f for f in self.fields_on_node(file_number, node)}
        out: list[PieceValue] = []
        raw_pieces = value.split("^")
        for idx, raw in enumerate(raw_pieces, start=1):
            spec = by_piece.get(idx)
            if spec is None:
                if raw == "":
                    continue
                out.append(PieceValue(piece=idx, raw=raw))
                continue
            out.append(
                PieceValue(
                    piece=idx,
                    raw=raw,
                    field_number=spec.number,
                    field_name=spec.name,
                    decoded=self._decode_piece(spec, raw),
                )
            )
        return out

    def _decode_piece(self, spec: FieldSpec, raw: str) -> str:
        if raw == "":
            return "<empty>"
        if spec.kind == "set of codes":
            return spec.set_of_codes.get(raw, f"{raw} (not a defined code)")
        if spec.kind == "date/time":
            iso = fileman_date(raw)
            return iso or raw
        if spec.kind == "pointer" and spec.pointer_file:
            target = self.file(spec.pointer_file)
            label = target.name if target else "?"
            resolved = ""
            if spec.pointer_root:
                node_ref = f"{spec.pointer_root}{raw},0)"
                val = self.sample_exact(node_ref)
                if val:
                    resolved = f" = {val.split('^')[0]}"
            return f"IEN {raw} in file #{spec.pointer_file} ({label}){resolved}"
        return raw

    def resolve(self, ref: str, sample_limit: int = 5) -> GlobalResolution:
        """Explain one global reference: which FileMan file, which fields, real data."""
        name, subs = parse_ref(ref)
        notes: list[str] = []
        canonical = f"{name}({','.join(subs)})" if subs else name
        probe = canonical if subs else f"{name}("
        match = self.match_root(probe)
        if match is None:
            match = self.match_root(f"{name}(")
        if match is None:
            notes.append(
                f"{name} is not a FileMan-managed global in this environment: no "
                'file has it as its ^DIC(file,0,"GL") root. It is most likely a '
                "scratch, index or application-private global."
            )
            return GlobalResolution(
                ref=canonical,
                global_name=name,
                subscripts=subs,
                fileman_file=None,
                matched_root="",
                record_ien="",
                node="",
                node_layout=[],
                schema=[],
                sample_values=self._raw_samples(name, sample_limit),
                notes=notes,
            )

        root, fnum = match
        fspec = self.file(fnum)
        # Subscripts the root itself pins down: ^VA(200, fixes the first one.
        root_subs = [s for s in root[len(name) + 1:].split(",") if s]
        rest = subs[len(root_subs):]
        record_ien = rest[0] if rest else ""
        node = rest[1] if len(rest) > 1 else ""
        cur_file = fnum

        if len(rest) > 2:
            # Deeper reference: a nested multiple, e.g. ^DPT(3,.03,1,0).
            walked = self._walk_multiple(fnum, rest)
            if walked:
                cur_file, node, note = walked
                notes.append(note)
            else:
                node = rest[-1]
                notes.append(
                    f"Reference is {len(rest)} levels deep; the intermediate "
                    "subscripts could not be matched to a FileMan multiple, so "
                    f"the layout shown is for node {node!r} of file #{cur_file}."
                )

        schema = self.fields(cur_file)
        layout = self.fields_on_node(cur_file, node) if node else []
        if node and not layout:
            notes.append(
                f"No field in file #{cur_file} declares storage on node {node!r}. "
                "It is probably a cross-reference or index node rather than "
                "record data."
            )

        samples = self._resolved_samples(root, cur_file, node, canonical, sample_limit)
        if not samples:
            notes.append("No populated sample nodes for this global in the cache.")
        if fspec and fspec.root:
            notes.append(
                f"{fspec.root} is FileMan file #{fspec.number} ({fspec.name})."
            )
        return GlobalResolution(
            ref=canonical,
            global_name=name,
            subscripts=subs,
            fileman_file=self.file(cur_file) or fspec,
            matched_root=root,
            record_ien=record_ien,
            node=node,
            node_layout=layout,
            schema=schema,
            sample_values=samples,
            notes=notes,
        )

    def _walk_multiple(
        self, fnum: str, rest: list[str]
    ) -> tuple[str, str, str] | None:
        """Follow ``ien, <multiple-node>, ien, node`` into a subfile."""
        cur = fnum
        i = 1  # rest[0] is the record IEN
        trail: list[str] = []
        while i + 1 < len(rest):
            sub_node = rest[i]
            match = next(
                (
                    f for f in self.fields(cur)
                    if f.subfile and f.node == sub_node
                ),
                None,
            )
            if match is None:
                return None
            trail.append(f"{match.name} (file #{match.subfile})")
            cur = match.subfile
            i += 2
        node = rest[-1] if i >= len(rest) else rest[i]
        return cur, node, "Descends through multiple: " + " -> ".join(trail)

    def _resolved_samples(
        self, root: str, file_number: str, node: str, canonical: str, limit: int
    ) -> list[SampleValue]:
        rows = self._samples.get(root, [])
        exact = [(r, v) for r, v in rows if r == canonical]
        if node:
            same_node = [
                (r, v) for r, v in rows
                if r != canonical and r.rstrip(")").rsplit(",", 1)[-1] == node
            ]
        else:
            same_node = [(r, v) for r, v in rows if r != canonical]
        chosen = (exact + same_node)[:limit]
        out: list[SampleValue] = []
        for r, v in chosen:
            n = r.rstrip(")").rsplit(",", 1)[-1]
            out.append(
                SampleValue(
                    ref=r,
                    value=v,
                    pieces=self.describe_value(file_number, n or node, v),
                )
            )
        return out

    def _raw_samples(self, name: str, limit: int) -> list[SampleValue]:
        prefix = f"{name}("
        out: list[SampleValue] = []
        for root, rows in self._samples.items():
            if not root.startswith(prefix):
                continue
            for r, v in rows:
                out.append(SampleValue(ref=r, value=v, pieces=[]))
                if len(out) >= limit:
                    return out
        return out

    # -- used by verify_change's divergence explainer ----------------------

    def explain_node_change(
        self, ref: str, expected: str, actual: str
    ) -> list[str]:
        """Name the FileMan fields whose stored value moved between two runs.

        Returns human-readable lines such as
        ``piece 2 (SEX, file #2 PATIENT): expected "M" (MALE), got "" (<empty>)``.
        Empty list if the global is not FileMan-managed.
        """
        try:
            res = self.resolve(ref, sample_limit=0)
        except ValueError:
            return []
        if res.fileman_file is None or not res.node_layout:
            return []
        by_piece = {f.piece: f for f in res.node_layout}
        exp_parts = expected.split("^")
        act_parts = actual.split("^")
        lines: list[str] = []
        for i in range(max(len(exp_parts), len(act_parts))):
            e = exp_parts[i] if i < len(exp_parts) else ""
            a = act_parts[i] if i < len(act_parts) else ""
            if e == a:
                continue
            spec = by_piece.get(i + 1)
            f = res.fileman_file
            if spec is None:
                lines.append(
                    f'piece {i + 1} (no FileMan field declared): '
                    f'expected "{e}", got "{a}"'
                )
                continue
            lines.append(
                f'piece {i + 1} ({spec.name}, field {spec.number} of file '
                f'#{f.number} {f.name}): expected "{e}" '
                f'({self._decode_piece(spec, e)}), got "{a}" '
                f'({self._decode_piece(spec, a)})'
            )
        return lines


@lru_cache(maxsize=4)
def _load_cached(path_str: str) -> DataDictionary:
    return DataDictionary.load(Path(path_str))


def default_dictionary(path: Path | None = None) -> DataDictionary:
    """Process-wide FileMan dictionary, loaded once.

    Degrades to an empty dictionary — never raises — when the cache is missing,
    so the other four static tools keep working in a checkout without it.
    """
    configured = os.environ.get("ROSETTA_FILEMAN_CACHE")
    if path is None and configured is None and os.environ.get("ROSETTA_CORPUS_DIR"):
        return DataDictionary.empty()
    if path is None and configured and configured.lower() in {"off", "none", "0"}:
        return DataDictionary.empty()
    try:
        return _load_cached(str(Path(path or configured or DEFAULT_CACHE_PATH).expanduser()))
    except FileNotFoundError:
        if configured:
            raise
        return DataDictionary.empty()


def iter_field_names(fields: Iterable[FieldSpec]) -> list[str]:
    return [f.name for f in fields]
