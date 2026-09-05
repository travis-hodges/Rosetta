"""Offset-preserving MUMPS line/command model for the mutation generator.

This module is deliberately thin. All the genuinely hard lexing -- string
masking, comment stripping and depth-zero splitting -- already exists and is
unit-tested in :mod:`rosetta.bench.select`; it is imported and reused here
rather than reimplemented. What this module adds is *positions*: every command
verb, postconditional and argument list is recorded as a ``(start, end)`` span
into the raw source line, so a mutation operator can splice a replacement
without re-parsing.

Why offsets work at all: :func:`rosetta.bench.select.mask_strings` replaces
string-literal *content* with ``\\x01`` placeholders one-for-one, so the masked
line and the raw line have identical length and identical character offsets.
Operators locate a target in the masked text and edit the raw text at the same
offsets. Nothing inside a string literal can ever be selected as a target.

Key MUMPS facts encoded here (see docs/PROJECT.md section 14):

* Entry labels sit in column 1; a line starting with whitespace is body only.
* A line's leading whitespace and ``.`` dot-level are syntactically meaningful:
  dots mark nesting under an argumentless ``DO``.
* An argumentless command is marked by a *second* space, which survives
  ``split_top`` as an empty token.
* Postconditionals attach to a command verb with ``:`` (``SET:X>3 Y=1``) and,
  separately, to individual ``DO``/``GOTO`` arguments (``D TAG^ROU:$D(X)``).
* Pattern-match operands (``X?3N1"-"4N``) contain bare letters that are pattern
  codes, not variable names; :func:`scan_pattern` exists so identifier scanning
  can skip them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator, Sequence

# Reuse, do not reimplement. `_matching_paren` is private to select.py but is
# the same routine we would otherwise copy; importing keeps one implementation.
from rosetta.bench.select import (
    COMMANDS,
    mask_strings,
    split_top,
    strip_comment,
    _matching_paren as matching_paren,
)

__all__ = [
    "MASK",
    "ARG_TAKING",
    "TP_COMMANDS",
    "Command",
    "MLine",
    "Routine",
    "matching_paren",
    "parse_routine",
    "parse_line",
    "split_top_spans",
    "find_call_spans",
    "CallSite",
    "scan_pattern",
    "identifier_occurrences",
    "Occurrence",
    "declared_locals",
    "has_tp_command",
    "assert_no_tp_command",
    "splice",
    "TPCommandError",
]

MASK = "\x01"

_LABEL_RE = re.compile(r"^([%A-Za-z][A-Za-z0-9]*)(?:\(([^)]*)\))?")
_CMD_RE = re.compile(r"^([A-Za-z%]+)(:.*)?$", re.DOTALL)
_IDENT_RE = re.compile(r"[%A-Za-z][A-Za-z0-9]*")

#: Commands that consume the following space-delimited token as their argument
#: list. Mirrors ``select._ARG_TAKING``; kept local so this module has no
#: dependency on a private name whose meaning could drift.
ARG_TAKING = frozenset(
    {
        "BREAK", "CLOSE", "DO", "FOR", "GOTO", "HALT", "IF", "JOB", "KILL",
        "LOCK", "MERGE", "NEW", "OPEN", "QUIT", "READ", "SET", "TSTART",
        "USE", "VIEW", "WRITE", "XECUTE", "ZALLOCATE", "ZDEALLOCATE",
        "ZKILL", "ZSYSTEM", "ZWRITE",
    }
)

#: Transaction-processing commands. The verifier runs every candidate inside its
#: own YottaDB TP frame; a candidate emitting ``TCOMMIT`` would silently commit
#: that frame to the live database with no error raised. Real VistA contains
#: zero command-position TP commands, so no legitimate mutation needs one.
TP_COMMANDS = frozenset({"TSTART", "TCOMMIT", "TROLLBACK", "TRESTART"})


class TPCommandError(AssertionError):
    """Raised when MUMPS source contains a command-position TP command."""


# --------------------------------------------------------------------------
# Line / command model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Command:
    """One command on one line, with spans into the raw line.

    All spans are ``(start, end)`` half-open character offsets into
    :attr:`MLine.raw`.

    ``postcond_span`` covers the colon as well as the condition, so dropping a
    postconditional is a splice of ``""`` over that span. ``postcond`` is the
    condition text without the leading colon.
    """

    canon: str
    verb: str
    verb_span: tuple[int, int]
    postcond: str | None = None
    postcond_span: tuple[int, int] | None = None
    args: str | None = None
    args_span: tuple[int, int] | None = None

    @property
    def has_args(self) -> bool:
        return self.args_span is not None


@dataclass(frozen=True)
class MLine:
    """One physical source line.

    ``code_span`` is the executable region: after any label and formal list,
    before any comment. Everything outside it is untouchable by an operator.
    """

    lineno: int  # 1-based
    raw: str
    masked: str
    label: str | None
    formals: tuple[str, ...]
    code_span: tuple[int, int]
    dot_level: int
    commands: tuple[Command, ...]

    @property
    def code(self) -> str:
        return self.masked[self.code_span[0]: self.code_span[1]]

    @property
    def is_code(self) -> bool:
        return bool(self.code.strip(". \t"))


@dataclass
class Routine:
    """A parsed routine: its lines plus label spans and local inventory."""

    name: str
    src: str
    lines: list[MLine] = field(default_factory=list)
    #: label -> (first_lineno, last_lineno) inclusive, 1-based
    label_spans: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: label -> formal argument names
    formals: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def label_at(self, lineno: int) -> str | None:
        """The label whose span contains ``lineno``, or ``None`` for the header."""
        for label, (lo, hi) in self.label_spans.items():
            if lo <= lineno <= hi:
                return label
        return None

    def lines_in(self, label: str) -> list[MLine]:
        lo, hi = self.label_spans[label]
        return [ln for ln in self.lines if lo <= ln.lineno <= hi]

    def line(self, lineno: int) -> MLine:
        return self.lines[lineno - 1]


def split_top_spans(text: str, sep: str, base: int = 0) -> list[tuple[str, int, int]]:
    """``split_top`` with offsets.

    ``sep.join(parts) == text`` holds for :func:`rosetta.bench.select.split_top`,
    so part offsets are just the running sum of lengths. Returns
    ``(part, start, end)`` with offsets shifted by ``base``.
    """
    parts = split_top(text, sep)
    out: list[tuple[str, int, int]] = []
    off = base
    for p in parts:
        out.append((p, off, off + len(p)))
        off += len(p) + len(sep)
    return out


def parse_line(raw: str, lineno: int) -> MLine:
    """Parse one raw source line into an :class:`MLine`."""
    masked = mask_strings(raw)
    label: str | None = None
    formals: tuple[str, ...] = ()
    body_start = 0
    if raw[:1] not in ("", " ", "\t"):
        lm = _LABEL_RE.match(masked)
        if lm:
            label = lm.group(1).upper()
            if lm.group(2) is not None:
                formals = tuple(
                    p.strip() for p in lm.group(2).split(",") if p.strip()
                )
            body_start = lm.end()

    code = strip_comment(masked[body_start:])
    code_end = body_start + len(code)

    dot_level = 0
    for ch in masked[body_start:code_end]:
        if ch == ".":
            dot_level += 1
        elif ch not in " \t":
            break

    commands = _parse_commands(masked, body_start, code_end)
    return MLine(
        lineno=lineno,
        raw=raw,
        masked=masked,
        label=label,
        formals=formals,
        code_span=(body_start, code_end),
        dot_level=dot_level,
        commands=tuple(commands),
    )


def _parse_commands(masked: str, start: int, end: int) -> list[Command]:
    parts = split_top_spans(masked[start:end], " ", base=start)
    out: list[Command] = []
    i = 0
    n = len(parts)
    while i < n:
        part, ps, _pe = parts[i]
        stripped = part.lstrip(". \t")
        if not stripped:
            i += 1
            continue
        lead = len(part) - len(stripped)
        stripped = stripped.rstrip()
        m = _CMD_RE.match(stripped)
        canon = COMMANDS.get(m.group(1).upper()) if m else None
        if canon is None or m is None:
            # Not a command verb: a stray expression token, or a construct this
            # parser does not model. Skip it -- operators only ever target
            # spans they positively identified.
            i += 1
            continue
        verb_start = ps + lead
        verb_end = verb_start + len(m.group(1))
        pc = m.group(2)
        pc_span = (verb_end, verb_start + len(stripped)) if pc else None
        args = None
        args_span = None
        if canon in ARG_TAKING and i + 1 < n and parts[i + 1][0].strip(". \t"):
            araw, as_, _ae = parts[i + 1]
            a_lead = len(araw) - len(araw.lstrip())
            atxt = araw.strip()
            args = atxt
            args_span = (as_ + a_lead, as_ + a_lead + len(atxt))
            i += 2
        else:
            i += 1
        out.append(
            Command(
                canon=canon,
                verb=m.group(1),
                verb_span=(verb_start, verb_end),
                postcond=pc[1:] if pc else None,
                postcond_span=pc_span,
                args=args,
                args_span=args_span,
            )
        )
    return out


def parse_routine(name: str, src: str) -> Routine:
    """Parse a whole ``.m`` source into a :class:`Routine`.

    ``name`` is the MUMPS routine name (``XLFSTR``), ``src`` its full text.
    """
    lines = [parse_line(raw, i + 1) for i, raw in enumerate(src.splitlines())]
    label_spans: dict[str, tuple[int, int]] = {}
    formals: dict[str, tuple[str, ...]] = {}
    open_label: str | None = None
    open_at = 0
    for ln in lines:
        if ln.label is None:
            continue
        if open_label is not None:
            label_spans[open_label] = (open_at, ln.lineno - 1)
        open_label = ln.label
        open_at = ln.lineno
        formals[ln.label] = ln.formals
    if open_label is not None:
        label_spans[open_label] = (open_at, len(lines))
    return Routine(
        name=name.upper(),
        src=src,
        lines=lines,
        label_spans=label_spans,
        formals=formals,
    )


# --------------------------------------------------------------------------
# TP-command guard
# --------------------------------------------------------------------------


def has_tp_command(src: str) -> bool:
    """True if ``src`` uses TSTART/TCOMMIT/TROLLBACK/TRESTART in command position.

    Substring matches inside comments and string literals are correctly
    ignored: the check runs over the parsed command list, not the raw text.
    """
    for i, raw in enumerate(src.splitlines()):
        for cmd in parse_line(raw, i + 1).commands:
            if cmd.canon in TP_COMMANDS:
                return True
    return False


def assert_no_tp_command(src: str, context: str = "source") -> None:
    """Raise :class:`TPCommandError` if ``src`` contains a TP command.

    Called on every mutant before it is admitted. A candidate emitting
    ``TCOMMIT`` would commit the verifier's own transaction frame to the live
    database silently; this is the last line of defence and must never be
    downgraded to a warning.
    """
    if has_tp_command(src):
        raise TPCommandError(
            f"{context} contains a command-position TP command "
            f"({'/'.join(sorted(TP_COMMANDS))}); refusing to emit it. "
            "The verifier runs candidates inside a TP frame."
        )


# --------------------------------------------------------------------------
# Pattern-match operands
# --------------------------------------------------------------------------

_PATTERN_CODES = "ANLUPCEanlupce"


def scan_pattern(text: str, i: int) -> int:
    """Return the index just past the MUMPS pattern starting at ``text[i]``.

    ``text`` must already be string-masked. A pattern is a sequence of atoms,
    each a repetition count (``3``, ``1.4``, ``.``, or empty) followed by either
    a run of pattern codes (``N``, ``A``, ``U``, ``L``, ``P``, ``C``, ``E``), a
    string literal, or a parenthesised alternation. Returns ``i`` unchanged if
    nothing pattern-shaped is there.

    Needed because pattern codes are bare letters -- ``X?12UN`` contains no
    variable named ``UN`` -- and a naive identifier scan would corrupt them.
    """
    n = len(text)
    j = i
    progressed = False
    while j < n:
        k = j
        while k < n and (text[k].isdigit() or text[k] == "."):
            k += 1
        if k < n and text[k] == "(":
            close = matching_paren(text, k)
            if close < 0:
                break
            j = close + 1
            progressed = True
            continue
        if k < n and text[k] == '"':
            k += 1
            while k < n and text[k] != '"':
                k += 1
            if k >= n:
                break
            j = k + 1
            progressed = True
            continue
        start_codes = k
        while k < n and text[k] in _PATTERN_CODES:
            k += 1
        if k == start_codes:
            break
        j = k
        progressed = True
    return j if progressed else i


# --------------------------------------------------------------------------
# Identifiers and locals
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Occurrence:
    """One identifier occurrence in an expression, as a span into the raw line."""

    name: str
    start: int
    end: int


def identifier_occurrences(masked: str, start: int, end: int) -> list[Occurrence]:
    """Bare identifiers in ``masked[start:end]`` that could be local variables.

    Excludes globals (``^X``), intrinsics and special variables (``$X``, ``$$T``),
    entry-reference tags (``TAG^ROU``), and pattern-match codes. Offsets are
    absolute, so they index the raw line directly.

    This is a *superset* filter -- it does not know what is in scope. Intersect
    with :func:`declared_locals` before treating a name as a variable.
    """
    out: list[Occurrence] = []
    i = start
    while i < end:
        ch = masked[i]
        if ch == '"':
            i += 1
            while i < end and masked[i] != '"':
                i += 1
            i += 1
            continue
        if ch == "?":
            j = scan_pattern(masked, i + 1)
            i = j if j > i + 1 else i + 1
            continue
        if ch == "^":
            # global or entryref: skip the name that follows
            i += 1
            m = _IDENT_RE.match(masked, i)
            i = m.end() if m else i
            continue
        if ch == "$":
            i += 1
            if i < end and masked[i] == "$":
                i += 1
            m = _IDENT_RE.match(masked, i)
            i = m.end() if m else i
            continue
        m = _IDENT_RE.match(masked, i)
        if m:
            nxt = m.end()
            if nxt < len(masked) and masked[nxt] == "^":
                i = nxt  # entryref tag; the '^' branch eats the routine name
                continue
            out.append(Occurrence(m.group(0), m.start(), m.end()))
            i = nxt
            continue
        i += 1
    return out


def _set_targets(args: str) -> list[str]:
    """Left-hand-side text of each ``SET`` argument (handles ``S (A,B)=1``)."""
    out: list[str] = []
    for a in split_top(args, ","):
        eq = _assign_eq(a)
        if eq < 0:
            continue
        lhs = a[:eq].strip()
        if lhs.startswith("(") and lhs.endswith(")"):
            out.extend(t.strip() for t in split_top(lhs[1:-1], ","))
        else:
            out.append(lhs)
    return out


def _assign_eq(arg: str) -> int:
    """Index of the assignment ``=`` in a ``SET`` argument, or ``-1``.

    Skips ``=`` that is part of a relational operator (``'=``, ``<=``... which
    MUMPS spells ``'>``) and any ``=`` nested in parentheses or a literal.
    """
    depth = 0
    in_str = False
    for i, ch in enumerate(arg):
        if ch == '"':
            in_str = not in_str
        elif in_str:
            continue
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "=" and depth == 0 and (i == 0 or arg[i - 1] not in "'<>=["):
            return i
    return -1


def declared_locals(routine: Routine, label: str | None = None) -> set[str]:
    """Local variable names declared in ``routine`` (optionally one label span).

    A name counts as declared if it is a formal argument, appears in a ``NEW``
    list, or is the target of ``SET``/``FOR``/``READ``/``MERGE``. Restricting
    identifier scanning to this set is what keeps pattern codes, intrinsic
    names and stray tokens out of the variable inventory.
    """
    lines = routine.lines_in(label) if label else routine.lines
    names: set[str] = set()
    if label:
        names.update(routine.formals.get(label, ()))
    else:
        for f in routine.formals.values():
            names.update(f)
    for ln in lines:
        for cmd in ln.commands:
            if cmd.args is None:
                continue
            if cmd.canon == "NEW":
                for a in split_top(cmd.args, ","):
                    a = a.strip().lstrip("(").rstrip(")")
                    m = _IDENT_RE.fullmatch(a)
                    if m:
                        names.add(a)
            elif cmd.canon == "SET":
                for lhs in _set_targets(cmd.args):
                    m = _IDENT_RE.match(lhs)
                    if m and m.start() == 0 and not lhs.startswith("^"):
                        names.add(m.group(0))
            elif cmd.canon == "FOR":
                head = split_top(cmd.args, "=")[0].strip()
                m = _IDENT_RE.fullmatch(head)
                if m:
                    names.add(head)
            elif cmd.canon in ("READ", "MERGE"):
                for a in split_top(cmd.args, ","):
                    a = a.strip()
                    if cmd.canon == "MERGE":
                        a = split_top(a, "=")[0].strip()
                    m = _IDENT_RE.match(a)
                    if m and m.start() == 0 and not a.startswith("^"):
                        names.add(m.group(0))
    return names


# --------------------------------------------------------------------------
# Call sites
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CallSite:
    """A call with an actual-argument list, located in a raw line.

    ``kind`` is ``"extrinsic"`` (``$$TAG^ROU(...)``), ``"do"`` (a ``DO``/``GOTO``
    argument) or ``"intrinsic"`` (``$TR(...)`` and friends). ``args`` holds the
    depth-zero argument spans, in order.
    """

    kind: str
    name: str
    name_span: tuple[int, int]
    paren_span: tuple[int, int]  # covers "(" .. ")"
    args: tuple[tuple[str, int, int], ...]


_EXTRINSIC_RE = re.compile(r"\$\$([%A-Za-z][A-Za-z0-9]*)?(\^[%A-Za-z][A-Za-z0-9]*)?")


def find_call_spans(
    line: MLine,
    intrinsics: Sequence[str] = (),
) -> list[CallSite]:
    """Locate call sites with a parenthesised actual-argument list on ``line``.

    ``intrinsics`` optionally names intrinsic functions (``$TR``, ``$P``) to
    report as well; by default only user-defined calls are returned.
    """
    masked = line.masked
    lo, hi = line.code_span
    out: list[CallSite] = []

    for m in _EXTRINSIC_RE.finditer(masked, lo, hi):
        if not (m.group(1) or m.group(2)):
            continue
        open_idx = m.end()
        if open_idx >= hi or masked[open_idx] != "(":
            continue
        close = matching_paren(masked, open_idx)
        if close < 0 or close >= hi:
            continue
        inner = masked[open_idx + 1: close]
        if not inner.strip():
            continue
        out.append(
            CallSite(
                kind="extrinsic",
                name=(m.group(1) or "") + (m.group(2) or ""),
                name_span=(m.start(), m.end()),
                paren_span=(open_idx, close + 1),
                args=tuple(split_top_spans(inner, ",", base=open_idx + 1)),
            )
        )

    for cmd in line.commands:
        if cmd.canon not in ("DO", "GOTO", "JOB") or cmd.args_span is None:
            continue
        astart, aend = cmd.args_span
        for _arg, s, e in split_top_spans(masked[astart:aend], ",", base=astart):
            seg = masked[s:e]
            open_rel = seg.find("(")
            if open_rel < 0:
                continue
            open_idx = s + open_rel
            close = matching_paren(masked, open_idx)
            if close < 0 or close >= e:
                continue
            inner = masked[open_idx + 1: close]
            if not inner.strip():
                continue
            out.append(
                CallSite(
                    kind="do",
                    name=seg[:open_rel].strip(),
                    name_span=(s, open_idx),
                    paren_span=(open_idx, close + 1),
                    args=tuple(split_top_spans(inner, ",", base=open_idx + 1)),
                )
            )

    for fn in intrinsics:
        pat = re.compile(re.escape(fn) + r"(?=\()", re.IGNORECASE)
        for m in pat.finditer(masked, lo, hi):
            open_idx = m.end()
            close = matching_paren(masked, open_idx)
            if close < 0 or close >= hi:
                continue
            inner = masked[open_idx + 1: close]
            if not inner.strip():
                continue
            out.append(
                CallSite(
                    kind="intrinsic",
                    name=m.group(0),
                    name_span=(m.start(), m.end()),
                    paren_span=(open_idx, close + 1),
                    args=tuple(split_top_spans(inner, ",", base=open_idx + 1)),
                )
            )

    out.sort(key=lambda c: c.name_span[0])
    return out


# --------------------------------------------------------------------------
# Editing
# --------------------------------------------------------------------------


def splice(text: str, start: int, end: int, replacement: str) -> str:
    """Replace ``text[start:end]`` with ``replacement``. Fails loudly on bad spans."""
    if not 0 <= start <= end <= len(text):
        raise ValueError(f"span ({start}, {end}) out of range for length {len(text)}")
    return text[:start] + replacement + text[end:]


def replace_line(src: str, lineno: int, new_line: str | None) -> str:
    """Return ``src`` with 1-based ``lineno`` replaced, or deleted if ``None``."""
    lines = src.splitlines()
    if not 1 <= lineno <= len(lines):
        raise ValueError(f"line {lineno} out of range (1..{len(lines)})")
    if new_line is None:
        del lines[lineno - 1]
    else:
        lines[lineno - 1] = new_line
    return "\n".join(lines) + ("\n" if src.endswith("\n") else "")


def iter_code_lines(routine: Routine) -> Iterator[MLine]:
    """Yield the lines of ``routine`` that contain executable commands."""
    for ln in routine.lines:
        if ln.commands:
            yield ln
