"""Static analysis and tractability ranking of VistA MUMPS routines.

Stdlib only, Python 3.11+. Emits plain dicts / JSON — this module deliberately
does not import ``rosetta.core.interface``; the frozen contract is owned
elsewhere.

What it extracts per routine (``analyse_source``):

* entry labels and their formal arguments
* routines called via ``DO`` / ``GOTO`` / ``JOB`` / ``$$`` extrinsic / ``$TEXT``
* distinct global references, split into reads and writes
* naked references (``^(...)``) counted separately — they are ambiguous, because
  the subscripts come from whatever global reference executed last
* line counts, plus the I/O, indirection and non-determinism facts the ranking
  needs

Accounting is per *label*, not just per routine, and is propagated along the
intra-routine ``DO TAG`` call graph to a fixpoint. VistA routines are grab-bags:
``XLFDT`` holds pure date arithmetic *and* a ``$HOROLOG``-reading ``NOW``, and
only label-level facts can tell the two apart.

What it does with that (``score_routine`` / ``rank_routines``):

Ranks by tractability per PROJECT.md section 9 — prefer computational routines,
avoid RPC broker entry points, screen handlers, terminal I/O and job spawning;
score by call-graph fan-out and distinct global references, both lower-better.
Every score component is emitted so the ranking is auditable by hand.

CLI::

    python -m rosetta.bench.select --extract --source-dir data/routines
    python -m rosetta.bench.select --source-dir DIR --out data/tasks/candidates.json

This module never writes ``data/tasks/split.lock.json``; the train/eval split is
written once, later, by ``rosetta.bench.build``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

__all__ = [
    "COMMANDS",
    "WEIGHTS",
    "mask_strings",
    "strip_comment",
    "split_top",
    "analyse_source",
    "analyse_directory",
    "routine_name_for",
    "extract_from_container",
    "add_call_graph",
    "load_entrypoints",
    "score_routine",
    "rank_routines",
    "build_report",
    "main",
]

# --------------------------------------------------------------------------
# MUMPS lexical constants
# --------------------------------------------------------------------------

#: Canonical command name keyed by the abbreviations VistA actually uses.
#: MUMPS allows any unambiguous prefix, but real VistA source sticks to the
#: one-letter form or the full word, so both are listed rather than resolved by
#: prefix matching.
COMMANDS: dict[str, str] = {}
for _canon, _forms in {
    "BREAK": ("B", "BREAK"),
    "CLOSE": ("C", "CLOSE"),
    "DO": ("D", "DO"),
    "ELSE": ("E", "ELSE"),
    "FOR": ("F", "FOR"),
    "GOTO": ("G", "GOTO"),
    "HALT": ("H", "HALT", "HANG"),  # H is HALT or HANG depending on arguments
    "IF": ("I", "IF"),
    "JOB": ("J", "JOB"),
    "KILL": ("K", "KILL"),
    "LOCK": ("L", "LOCK"),
    "MERGE": ("M", "MERGE"),
    "NEW": ("N", "NEW"),
    "OPEN": ("O", "OPEN"),
    "QUIT": ("Q", "QUIT"),
    "READ": ("R", "READ"),
    "SET": ("S", "SET"),
    "TCOMMIT": ("TC", "TCOMMIT"),
    "TRESTART": ("TRE", "TRESTART"),
    "TROLLBACK": ("TRO", "TROLLBACK"),
    "TSTART": ("TS", "TSTART"),
    "USE": ("U", "USE"),
    "VIEW": ("V", "VIEW"),
    "WRITE": ("W", "WRITE"),
    "XECUTE": ("X", "XECUTE"),
    "ZALLOCATE": ("ZA", "ZALLOCATE"),
    "ZDEALLOCATE": ("ZD", "ZDEALLOCATE"),
    "ZKILL": ("ZK", "ZKILL", "ZW", "ZWITHDRAW"),
    "ZSYSTEM": ("ZSY", "ZSYSTEM"),
    "ZWRITE": ("ZWR", "ZWRITE"),
}.items():
    for _f in _forms:
        COMMANDS[_f] = _canon

#: Commands that take arguments. Used to decide whether the token after a
#: command is that command's argument list or the next command. Argumentless
#: forms still parse correctly because MUMPS marks them with a second space,
#: which survives the splitter as an empty token.
_ARG_TAKING = frozenset(
    {
        "BREAK", "CLOSE", "DO", "FOR", "GOTO", "HALT", "IF", "JOB", "KILL",
        "LOCK", "MERGE", "NEW", "OPEN", "QUIT", "READ", "SET", "TSTART",
        "USE", "VIEW", "WRITE", "XECUTE", "ZALLOCATE", "ZDEALLOCATE",
        "ZKILL", "ZSYSTEM", "ZWRITE",
    }
)

_MASK = "\x01"  # placeholder standing in for string-literal content

_LABEL_RE = re.compile(r"^([%A-Za-z][A-Za-z0-9]*)(?:\(([^)]*)\))?")
_CMD_RE = re.compile(r"^([A-Za-z%]+)(:.*)?$", re.DOTALL)
_NAME_RE = re.compile(r"[%A-Za-z][A-Za-z0-9]*")
_TEXT_RE = re.compile(r"\$T(?:EXT)?\(")

#: ``TAG^ROU(params)`` with the optional ``+offset`` form. The argument-level
#: postconditional (``D TAG^ROU:cond``) is stripped before this is applied.
_ENTRYREF_RE = re.compile(
    r"^\s*(?P<tag>[%A-Za-z][A-Za-z0-9]*)?(?:\+[^^(]+)?"
    r"(?:\^(?P<rou>[%A-Za-z][A-Za-z0-9]*))?\s*(?:\((?P<params>.*)\))?\s*$",
    re.DOTALL,
)

#: Non-deterministic special variables. ``$J`` and ``$I`` need the lookahead:
#: ``$J(...)`` is ``$JUSTIFY`` (pure formatting), bare ``$J`` is the process id.
_NONDET_SVN = re.compile(
    r"\$(?:H(?:OROLOG)?|ZH(?:OROLOG)?|ZUT|ZTIMEOUT|ZPOS|ZJOB|ZDATEFORM)\b"
    r"|\$R(?:ANDOM)?\("
    r"|\$J(?:OB)?\b(?!\()"
    r"|\$I(?:O)?\b(?!\()"
)
#: Entry references whose value is wall-clock time.
_NONDET_ENTRYREFS = frozenset(
    {
        "NOW^%DTC", "NOW^XLFDT", "NOW^DIQ", "H^%DTC", "HTFM^XLFDT",
        "DT^XLFDT", "TIME^XLFDT", "DT^DICRW", "T^%DTC",
    }
)
#: Globals whose value is environment or wall-clock state.
_ENV_GLOBALS = frozenset({"%ZOSF", "%ZTSCH", "%ZIS", "%ZTER"})
#: Process-scoped scratch globals. Usually subscripted by ``$J``, so their keys
#: differ between runs; usable, but the verifier must normalise them.
_SCRATCH_GLOBALS = frozenset({"TMP", "XTMP", "UTILITY", "XUTL", "DISV", "%ZOSV"})

#: Interactive / device / taskman callouts. These make a label unusable.
_INTERACTIVE_ROUTINES = frozenset(
    {
        "DIR", "DIC", "DIE", "DDS", "DDSU", "DDIOL", "DIWP", "DIWW",
        "%ZIS", "%ZISC", "%ZIS1", "%ZISS", "%ZISH", "%ZTLOAD", "%ZTLOA",
        "%ZTM", "%ZTER", "XQ1", "XQ2", "XQ3", "XQ4", "XQOR", "XUS",
        "VALM", "VALM1", "VALM4", "VALM10", "XGF", "%ZTP1",
    }
)
#: Namespaces that are, by name, broker / screen / device / taskman machinery.
_UI_ROUTINE_PREFIXES = ("XWB", "VALM", "XGF", "DDS", "DDG", "XQ", "%ZIS", "%ZT")

#: Routine-name shapes that are installers, checksums or pure data, not logic.
_NONCODE_NAME_RE = re.compile(r"(NTEG|INIT\d*|ENV|POST|PST|TXT|MSG)$")

#: KIDS patch machinery. An install routine runs once, at patch time, and its
#: whole job is side effects on the dictionary — useless as a repair target.
_INSTALL_HEADER_RE = re.compile(
    r"(POST[- ]?INSTALL|PRE[- ]?INSTALL|POST[- ]?INIT|PRE[- ]?INIT|"
    r"ENVIRONMENT CHECK|INSTALL ROUTINE|PATCH INSTALL)",
    re.IGNORECASE,
)
_INSTALL_ROUTINES = frozenset({"XPDUTL", "XPDID", "XPDIQ", "XPDIP", "XPDET"})

#: Cache ObjectScript leaking into a .m file (``D obj.Method("...")``, ``$$$OK``).
#: It sits in the routine directory but YottaDB cannot run it.
_NON_M_SYNTAX_RE = re.compile(r"\$\$\$[A-Za-z]|\.[A-Za-z]\w*\.[A-Za-z]\w*\(")

#: A line that only quits. A routine built almost entirely of these has been
#: gutted — YTSMPIR's scoring algorithm is proprietary and was stripped before
#: public release, leaving 13 bodyless tags. Nothing there to mutate.
_BARE_QUIT_RE = re.compile(r"Q(?:UIT)?(?::[^\s]+)?", re.IGNORECASE)

#: String / numeric primitives. Density of these is the "computational" signal.
_COMPUTATIONAL_FN_RE = re.compile(
    r"\$(?:P(?:IECE)?|E(?:XTRACT)?|L(?:ENGTH)?|S(?:ELECT)?|TR(?:ANSLATE)?|"
    r"F(?:IND)?|J(?:USTIFY)?|A(?:SCII)?|C(?:HAR)?|RE(?:VERSE)?|ZCONVERT|"
    r"FN(?:UMBER)?)\("
)

#: Label-level flags that make a label unusable as a benchmark entry point.
_BLOCKING_FLAGS = ("nondet:", "interactive:", "read", "device", "job", "xecute", "indirect")


# --------------------------------------------------------------------------
# Lexing helpers
# --------------------------------------------------------------------------


def mask_strings(line: str) -> str:
    """Replace double-quoted literal content with ``\\x01`` placeholders.

    Offsets and quote positions are preserved, but spaces, semicolons, carets
    and parentheses inside literals can no longer confuse the splitter. ``""``
    is MUMPS' escaped quote and is masked as content.
    """
    out: list[str] = []
    in_str = False
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == '"':
            if in_str and i + 1 < n and line[i + 1] == '"':
                out.append(_MASK * 2)
                i += 2
                continue
            in_str = not in_str
            out.append('"')
        else:
            out.append(_MASK if in_str else ch)
        i += 1
    return "".join(out)


def strip_comment(masked: str) -> str:
    """Truncate an already string-masked line at its first comment ``;``."""
    for i, ch in enumerate(masked):
        if ch == ";":
            return masked[:i]
    return masked


def split_top(text: str, sep: str) -> list[str]:
    """Split on ``sep`` at parenthesis depth zero, outside string literals."""
    parts: list[str] = []
    depth = 0
    in_str = False
    start = 0
    for i, ch in enumerate(text):
        if ch == '"':
            in_str = not in_str
        elif in_str:
            continue
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def _strip_arg_postcond(arg: str) -> str:
    """Drop a trailing argument-level postconditional (``D TAG^ROU:Y>0``)."""
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
        elif ch == ":" and depth == 0:
            return arg[:i]
    return arg


def _matching_paren(text: str, open_idx: int) -> int:
    """Index of the ``)`` matching the ``(`` at ``open_idx``, or ``-1``."""
    depth = 0
    in_str = False
    for i in range(open_idx, len(text)):
        ch = text[i]
        if ch == '"':
            in_str = not in_str
        elif in_str:
            continue
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


# --------------------------------------------------------------------------
# Per-routine accumulator
# --------------------------------------------------------------------------


class _Facts:
    """Mutable accumulator for one routine, with per-label attribution."""

    def __init__(self, name: str) -> None:
        self.name = name.upper()
        self.labels: list[dict[str, Any]] = []
        self.cur = ""  # label whose span we are inside ("" = routine header)
        self.calls: set[str] = set()
        self.entryrefs: set[str] = set()
        self.reads: set[str] = set()
        self.writes: set[str] = set()
        self.locks: set[str] = set()
        self.naked_read = 0
        self.naked_write = 0
        self.indirection = 0
        self.xecute = 0
        self.cmd_counts: dict[str, int] = {}
        self.flags: dict[str, set[str]] = {"": set()}
        self.local_calls: dict[str, set[str]] = {"": set()}
        self.span_lines: dict[str, int] = {"": 0}
        self.bare_quit_lines = 0

    def enter(self, label: str) -> None:
        self.cur = label
        self.flags.setdefault(label, set())
        self.local_calls.setdefault(label, set())
        self.span_lines.setdefault(label, 0)

    def flag(self, f: str) -> None:
        self.flags[self.cur].add(f)

    def cmd(self, canon: str) -> None:
        self.cmd_counts[canon] = self.cmd_counts.get(canon, 0) + 1


def _add_global(f: _Facts, name: str, write: bool) -> None:
    (f.writes if write else f.reads).add(name)
    if name in _ENV_GLOBALS:
        f.flag("nondet:^" + name)
    elif name in _SCRATCH_GLOBALS:
        f.flag("scratch:^" + name)


def _note_entryref(f: _Facts, tag: str, rou: str) -> None:
    ref = f"{tag}^{rou}" if tag else "^" + rou
    f.entryrefs.add(ref)
    f.calls.add(rou)
    if rou in _INTERACTIVE_ROUTINES:
        f.flag("interactive:" + ref)
    if ref.lstrip("^") in _NONDET_ENTRYREFS or ref in _NONDET_ENTRYREFS:
        f.flag("nondet:" + ref)


def _scan_expr(f: _Facts, text: str, write: bool = False) -> None:
    """Scan an expression for global references, extrinsics and indirection.

    ``write`` marks the fragment as a ``SET`` left-hand side or ``KILL``
    argument. Subscripts nested inside a write target are strictly reads, but
    treating the fragment uniformly keeps the pass linear and costs almost
    nothing at corpus scale.
    """
    if not text:
        return
    up = text.upper()
    for m in _NONDET_SVN.finditer(up):
        f.flag("nondet:" + m.group(0).rstrip("("))
    if "@" in text:
        f.indirection += text.count("@")
        f.flag("indirect")

    # $TEXT(...) names a routine, not a global. Consume the whole call.
    while True:
        m = _TEXT_RE.search(text)
        if not m:
            break
        close = _matching_paren(text, m.end() - 1)
        inner = text[m.end():close] if close > 0 else text[m.end():]
        cm = re.search(r"\^([%A-Za-z][A-Za-z0-9]*)", inner)
        if cm:
            _note_entryref(f, "$TEXT", cm.group(1).upper())
        text = text[: m.start()] + " " + (text[close + 1:] if close > 0 else "")

    i = 0
    n = len(text)
    while True:
        i = text.find("^", i)
        if i < 0:
            break
        # Walk back over a label name to detect the $$ extrinsic form.
        j = i
        while j > 0 and (text[j - 1].isalnum() or text[j - 1] == "%"):
            j -= 1
        label = text[j:i].upper()
        if j >= 2 and text[j - 2:j] == "$$":
            rm = _NAME_RE.match(text[i + 1:])
            if rm:
                _note_entryref(f, label, rm.group(0).upper())
                i += 1 + len(rm.group(0))
            else:
                i += 1
            continue
        nxt = text[i + 1] if i + 1 < n else ""
        if nxt == "(":
            # Naked reference — subscripts inherited from the previous
            # reference, so which global this touches is not statically known.
            if write:
                f.naked_write += 1
            else:
                f.naked_read += 1
            i += 1
            continue
        if nxt == "|":  # extended reference ^|"env"|GLB(...)
            close = text.find("|", i + 2)
            rm = _NAME_RE.match(text[close + 1:]) if close > 0 else None
            if rm:
                _add_global(f, rm.group(0).upper(), write)
                i = close + 1 + len(rm.group(0))
                continue
            i += 1
            continue
        rm = _NAME_RE.match(text[i + 1:])
        if rm:
            _add_global(f, rm.group(0).upper(), write)
            i += 1 + len(rm.group(0))
        else:
            i += 1


def _scan_entryref(f: _Facts, arg: str, spawn: bool = False) -> None:
    """Parse one ``DO`` / ``GOTO`` / ``JOB`` argument as an entry reference."""
    arg = _strip_arg_postcond(arg).strip()
    if not arg:
        return
    if "@" in arg:
        f.indirection += arg.count("@")
        f.flag("indirect")
        _scan_expr(f, arg)
        return
    if arg.startswith("("):  # parameter list of an argumentless DO block
        _scan_expr(f, arg)
        return
    m = _ENTRYREF_RE.match(arg)
    if not m:
        _scan_expr(f, arg)
        return
    rou = m.group("rou")
    tag = (m.group("tag") or "").upper()
    if rou:
        _note_entryref(f, tag, rou.upper())
        if spawn:
            f.flag("job")
    elif tag:
        f.local_calls[f.cur].add(tag)
    if m.group("params"):
        _scan_expr(f, m.group("params"))


def _handle_args(f: _Facts, canon: str, args: str) -> None:
    if canon in ("DO", "GOTO", "JOB"):
        for a in split_top(args, ","):
            _scan_entryref(f, a, spawn=canon == "JOB")
        return
    if canon == "SET":
        for a in split_top(args, ","):
            eq = -1
            depth = 0
            in_str = False
            for i, ch in enumerate(a):
                if ch == '"':
                    in_str = not in_str
                elif in_str:
                    continue
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                elif ch == "=" and depth == 0 and (i == 0 or a[i - 1] not in "'<>=["):
                    eq = i
                    break
            if eq < 0:
                _scan_expr(f, a)
                continue
            lhs, rhs = a[:eq].strip(), a[eq + 1:]
            if lhs.startswith("(") and lhs.endswith(")"):
                for t in split_top(lhs[1:-1], ","):
                    _scan_expr(f, t, write=True)
            else:
                _scan_expr(f, lhs, write=True)
            _scan_expr(f, rhs)
        return
    if canon in ("KILL", "ZKILL"):
        if args.strip().startswith("("):  # exclusive kill of locals
            _scan_expr(f, args)
            return
        for t in split_top(args, ","):
            _scan_expr(f, t, write=True)
        return
    if canon == "MERGE":
        for a in split_top(args, ","):
            parts = split_top(a, "=")
            if len(parts) >= 2:
                _scan_expr(f, parts[0], write=True)
                _scan_expr(f, "=".join(parts[1:]))
            else:
                _scan_expr(f, a)
        return
    if canon in ("LOCK", "ZALLOCATE", "ZDEALLOCATE"):
        for m in re.finditer(r"\^([%A-Za-z][A-Za-z0-9]*)", args):
            f.locks.add(m.group(1).upper())
        return
    if canon == "NEW":
        return  # locals only
    if canon == "XECUTE":
        f.xecute += 1
        f.flag("xecute")
    elif canon == "READ":
        f.flag("read")
    elif canon in ("OPEN", "USE", "CLOSE", "VIEW"):
        f.flag("device")
    elif canon == "ZSYSTEM":
        f.flag("device")
    _scan_expr(f, args)


def _walk_body(f: _Facts, body: str) -> None:
    tokens = split_top(body, " ")
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i].lstrip(". \t")
        if not tok:
            i += 1
            continue
        m = _CMD_RE.match(tok)
        canon = COMMANDS.get(m.group(1).upper()) if m else None
        if canon is None:
            _scan_expr(f, tok)
            i += 1
            continue
        f.cmd(canon)
        if canon == "READ":
            f.flag("read")
        elif canon in ("OPEN", "USE", "CLOSE", "VIEW", "ZSYSTEM"):
            f.flag("device")
        elif canon == "JOB":
            f.flag("job")
        if m.group(2):
            _scan_expr(f, m.group(2)[1:])  # postconditional is a read expression
        if canon in _ARG_TAKING and i + 1 < n and tokens[i + 1].strip(". \t"):
            _handle_args(f, canon, tokens[i + 1].strip())
            i += 2
            continue
        i += 1


def _close_label_flags(f: _Facts) -> None:
    """Propagate flags along intra-routine ``DO TAG`` edges to a fixpoint."""
    changed = True
    guard = 0
    while changed and guard < 50:
        changed = False
        guard += 1
        for label, callees in f.local_calls.items():
            for callee in callees:
                src = f.flags.get(callee)
                if src and not src <= f.flags[label]:
                    f.flags[label] |= src
                    changed = True


def _first_comment(text: str) -> str:
    """The routine header comment — VistA's one-line description of itself."""
    first = text.splitlines()[0] if text else ""
    return first.split(";", 1)[1].strip()[:160] if ";" in first else ""


def _blocked(flags: set[str]) -> list[str]:
    return sorted(x for x in flags if x.startswith(_BLOCKING_FLAGS))


# --------------------------------------------------------------------------
# Public analysis API
# --------------------------------------------------------------------------


def analyse_source(name: str, text: str) -> dict[str, Any]:
    """Statically analyse one MUMPS routine; return a plain fact dict.

    ``name`` is the routine name (``XLFDT``), ``text`` its full ``.m`` source.
    Percent routines live on disk with a leading underscore — use
    ``routine_name_for`` to translate ``_DTC.m`` to ``%DTC``.
    """
    f = _Facts(name)
    lines = text.splitlines()
    code_lines = 0
    comment_lines = 0
    data_lines = 0
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        masked = mask_strings(line)
        body_start = 0
        if line[:1] not in (" ", "\t"):
            lm = _LABEL_RE.match(masked)
            if lm:
                label = lm.group(1).upper()
                formals = (
                    [p.strip() for p in lm.group(2).split(",") if p.strip()]
                    if lm.group(2) is not None
                    else []
                )
                f.enter(label)
                f.labels.append(
                    {"label": label, "formals": formals, "arity": len(formals)}
                )
                body_start = lm.end()
        rest = masked[body_start:]
        body = strip_comment(rest)
        if rest[len(body):].startswith(";;"):
            data_lines += 1
        if body.strip():
            code_lines += 1
            f.span_lines[f.cur] = f.span_lines.get(f.cur, 0) + 1
            if _BARE_QUIT_RE.fullmatch(body.strip()):
                f.bare_quit_lines += 1
            _walk_body(f, body)
        elif body_start == 0:
            comment_lines += 1

    _close_label_flags(f)

    for lab in f.labels:
        fl = f.flags.get(lab["label"], set())
        lab["flags"] = sorted(fl)
        lab["blocked_by"] = _blocked(fl)
        lab["clean"] = not lab["blocked_by"]

    reachable: set[str] = {l["label"] for l in f.labels if l["formals"] and l["clean"]}
    frontier = list(reachable)
    while frontier:
        cur = frontier.pop()
        for callee in f.local_calls.get(cur, ()):  # DO/GOTO TAG within routine
            if callee not in reachable and callee in f.span_lines:
                reachable.add(callee)
                frontier.append(callee)
    reachable_lines = sum(f.span_lines.get(l, 0) for l in reachable)

    all_flags: set[str] = set()
    for s in f.flags.values():
        all_flags |= s
    calls = sorted(f.calls - {f.name})
    extrinsic = [l["label"] for l in f.labels if l["formals"]]
    clean_extrinsic = [l["label"] for l in f.labels if l["formals"] and l["clean"]]
    return {
        "name": f.name,
        "description": _first_comment(text),
        "non_m_syntax": bool(_NON_M_SYNTAX_RE.search(text)),
        "lines_total": len(lines),
        "lines_code": code_lines,
        "lines_comment": comment_lines,
        "lines_data": data_lines,
        "labels": f.labels,
        "label_count": len(f.labels),
        "extrinsic_entries": extrinsic,
        "clean_extrinsic_entries": clean_extrinsic,
        "reachable_code_lines": reachable_lines,
        "bare_quit_lines": f.bare_quit_lines,
        "bare_quit_ratio": round(f.bare_quit_lines / code_lines, 3) if code_lines else 0.0,
        "entry_coverage": round(reachable_lines / code_lines, 3) if code_lines else 0.0,
        "calls": calls,
        "fanout": len(calls),
        "entryrefs": sorted(f.entryrefs),
        "globals_read": sorted(f.reads),
        "globals_written": sorted(f.writes),
        "global_count": len(f.reads | f.writes),
        "locks": sorted(f.locks),
        "naked_read": f.naked_read,
        "naked_write": f.naked_write,
        "naked_total": f.naked_read + f.naked_write,
        "indirection": f.indirection,
        "xecute": f.xecute,
        "commands": dict(sorted(f.cmd_counts.items())),
        "flags": sorted(all_flags),
        "nondeterminism": sorted(x[7:] for x in all_flags if x.startswith("nondet:")),
        "interactive_calls": sorted(
            x[12:] for x in all_flags if x.startswith("interactive:")
        ),
        "scratch_globals": sorted(
            x[8:] for x in all_flags if x.startswith("scratch:")
        ),
    }


def routine_name_for(path: Path) -> str:
    """Map a ``.m`` filename to its MUMPS routine name (``_DTC.m`` -> ``%DTC``)."""
    stem = path.stem
    return "%" + stem[1:] if stem.startswith("_") else stem


def analyse_directory(
    source_dir: Path, limit: int | None = None
) -> dict[str, dict[str, Any]]:
    """Analyse every ``.m`` file in ``source_dir``, keyed by routine name."""
    facts: dict[str, dict[str, Any]] = {}
    paths = sorted(source_dir.glob("*.m"))
    if limit:
        paths = paths[:limit]
    for p in paths:
        name = routine_name_for(p)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            facts[name] = {"name": name, "path": p.name, "analysis_error": str(exc)}
            continue
        try:
            rec = analyse_source(name, text)
        except Exception as exc:  # defensive: one bad file must not stop 39k
            rec = {"name": name, "analysis_error": f"{type(exc).__name__}: {exc}"}
        rec["path"] = p.name
        rec["bytes"] = p.stat().st_size
        facts[name] = rec
    return facts


def add_call_graph(
    facts: dict[str, dict[str, Any]], depth: int = 3, cap: int = 400
) -> None:
    """Annotate each routine with corpus-wide call-graph facts.

    Adds ``fanin``/``callers_sample`` plus *transitive* reach to ``depth`` hops.
    Direct fan-out badly understates real dependency: ``$$ORANGE^AJETIU4(DFN)``
    has fan-out 1 and zero global references of its own, but its single callee
    ``VADPT`` fans into ``VADPT0/1/4/5`` and reads most of the patient record.
    ``cap`` bounds the closure so a hub routine cannot blow up the pass.
    """
    callers: dict[str, set[str]] = {}
    for name, f in facts.items():
        for callee in f.get("calls", ()):
            callers.setdefault(callee, set()).add(name)
    for name, f in facts.items():
        c = callers.get(name, set())
        f["fanin"] = len(c)
        f["callers_sample"] = sorted(c)[:12]
        f["calls_resolved"] = sum(1 for x in f.get("calls", ()) if x in facts)

    for name, f in facts.items():
        seen: set[str] = set()
        frontier = {name}
        for _ in range(depth):
            nxt: set[str] = set()
            for r in frontier:
                for callee in facts.get(r, {}).get("calls", ()):
                    if callee not in seen:
                        seen.add(callee)
                        nxt.add(callee)
                if len(seen) >= cap:
                    break
            if len(seen) >= cap or not nxt:
                break
            frontier = nxt
        seen.discard(name)
        globs = set(f.get("globals_read", ())) | set(f.get("globals_written", ()))
        for r in seen:
            rf = facts.get(r)
            if rf:
                globs |= set(rf.get("globals_read", ())) | set(
                    rf.get("globals_written", ())
                )
        f["transitive_calls"] = len(seen)
        f["transitive_global_count"] = len(globs)


def extract_from_container(
    dest: Path, container: str = "vehu", routine_dir: str = "/home/vehu/r"
) -> int:
    """Copy every ``.m`` routine out of a running WorldVistA container.

    Read-only with respect to the container: runs ``find | tar`` as the ``vehu``
    user and streams the archive out. Returns the number of files present in
    ``dest`` afterwards.
    """
    from rosetta.core.source_io import extract_sources
    return extract_sources(dest, container, routine_dir)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

#: Additive score components. Higher total is a more tractable target.
WEIGHTS: dict[str, float] = {
    "base": 100.0,
    "per_fanout": -4.0,            # call-graph fan-out, lower better (section 9)
    "per_global": -3.5,            # distinct global references, lower better
    "per_naked": -6.0,             # naked refs are ambiguous statically
    "per_indirection": -5.0,       # @ indirection defeats static analysis
    "per_xecute": -8.0,            # XECUTE assembles code at runtime
    "per_scratch_global": -4.0,    # ^TMP/^XTMP keyed by $J: needs normalising
    "per_transitive_call": -0.15,  # routines reachable within 3 hops
    "per_transitive_global": -0.35,  # globals reachable within 3 hops
    "device_write_per_cmd": -0.4,  # WRITE to current device, capped
    "entry_hygiene_penalty": -18.0,    # scaled by the fraction of $$ entries
                                       # blocked by $H/$J/READ/device/XECUTE
    "clean_entry_bonus": 14.0,     # has a $$TAG^ROU(args) entry with no blockers
    "entry_coverage_bonus": 22.0,  # share of the routine's code actually reachable
                                   # from those entries — LEXXM2 exposes four
                                   # trivial helpers and hides its real logic
                                   # behind caller-scope-variable tags
    "pure_bonus": 12.0,            # touches no globals at all
    "read_only_bonus": 6.0,        # reads globals, writes none
    "computational_bonus": 10.0,   # dense in $PIECE/$EXTRACT/$SELECT
    "arity_bonus_per_entry": 0.8,  # more callable entries, capped
    "size_penalty_per_line": -0.06,   # outside the ideal band
}
IDEAL_LINES = (20, 260)
MAX_DEVICE_WRITE_PENALTY = -12.0
MAX_ARITY_BONUS = 10.0


def _exclusions(f: dict[str, Any], entrypoints: dict[str, set[str]]) -> list[str]:
    """Hard disqualifiers. A non-empty result removes the routine from ranking."""
    out: list[str] = []
    name = f["name"]
    cmds = f.get("commands", {})
    flags = set(f.get("flags", ()))
    if name in entrypoints["rpc"]:
        out.append("rpc_broker_entry_point")
    if name in entrypoints["option"]:
        out.append("menu_option_entry_point")
    if name in entrypoints["protocol"]:
        out.append("protocol_entry_point")
    if any(name.startswith(p) for p in _UI_ROUTINE_PREFIXES):
        out.append("ui_or_broker_namespace")
    if _NONCODE_NAME_RE.search(name):
        out.append("install_or_checksum_routine")
    if name in entrypoints.get("install", ()):
        out.append("kids_install_routine")
    elif _INSTALL_HEADER_RE.search(f.get("description", "")):
        out.append("install_routine_by_header")
    elif set(f.get("calls", ())) & _INSTALL_ROUTINES:
        out.append("calls_kids_installer")
    if f.get("non_m_syntax"):
        out.append("non_mumps_syntax")
    if cmds.get("READ") or "read" in flags:
        out.append("terminal_read")
    if cmds.get("OPEN") or cmds.get("USE") or cmds.get("CLOSE") or "device" in flags:
        out.append("device_io")
    if cmds.get("JOB") or "job" in flags:
        out.append("job_spawn")
    if cmds.get("ZSYSTEM"):
        out.append("shell_out")
    if f.get("interactive_calls"):
        out.append("interactive_callout")
    if f.get("xecute", 0) >= 2 or f.get("indirection", 0) >= 5:
        out.append("dynamic_code")
    if f.get("lines_code", 0) < 12:
        out.append("too_small")
    if f.get("lines_code", 0) > 400:
        out.append("too_large")
    if f.get("lines_data", 0) > f.get("lines_code", 1):
        out.append("mostly_data_lines")
    if not f.get("extrinsic_entries"):
        out.append("no_entry_with_formal_args")
    elif not f.get("clean_extrinsic_entries"):
        out.append("no_clean_callable_entry")
    elif f.get("bare_quit_ratio", 0.0) >= 0.5:
        # Most executable lines are argumentless QUITs: a gutted routine or a
        # commented-out shell. Distinct from a dense one-liner function library
        # like XLFMTH, whose 32 entries each QUIT a real expression.
        out.append("gutted_bodies")
    elif f.get("entry_coverage", 0.0) < 0.25:
        # Formal-arg entries exist but reach almost none of the routine: the
        # real logic sits behind tags that read caller-scope variables.
        out.append("logic_unreachable_from_entries")
    return out


def _computational_density(text: str) -> float:
    """Fraction of code lines using string/number primitives rather than I/O."""
    lines = [l for l in text.splitlines() if l[:1] in (" ", "\t") and l.strip()]
    if not lines:
        return 0.0
    return sum(1 for l in lines if _COMPUTATIONAL_FN_RE.search(l.upper())) / len(lines)


def score_routine(f: dict[str, Any], computational_density: float) -> dict[str, Any]:
    """Score one routine's tractability; return the total and its components."""
    c: dict[str, float] = {"base": WEIGHTS["base"]}
    c["fanout"] = WEIGHTS["per_fanout"] * f.get("fanout", 0)
    c["globals"] = WEIGHTS["per_global"] * f.get("global_count", 0)
    c["naked"] = WEIGHTS["per_naked"] * f.get("naked_total", 0)
    c["indirection"] = WEIGHTS["per_indirection"] * f.get("indirection", 0)
    c["xecute"] = WEIGHTS["per_xecute"] * f.get("xecute", 0)
    c["scratch_globals"] = WEIGHTS["per_scratch_global"] * len(
        f.get("scratch_globals", ())
    )
    c["transitive_calls"] = round(
        WEIGHTS["per_transitive_call"] * f.get("transitive_calls", 0), 2
    )
    c["transitive_globals"] = round(
        WEIGHTS["per_transitive_global"] * f.get("transitive_global_count", 0), 2
    )
    c["device_write"] = max(
        MAX_DEVICE_WRITE_PENALTY,
        WEIGHTS["device_write_per_cmd"] * f.get("commands", {}).get("WRITE", 0),
    )
    entries = f.get("extrinsic_entries", ())
    clean = f.get("clean_extrinsic_entries", ())
    blocked_frac = (len(entries) - len(clean)) / len(entries) if entries else 1.0
    c["entry_hygiene"] = round(WEIGHTS["entry_hygiene_penalty"] * blocked_frac, 2)
    c["clean_entry"] = WEIGHTS["clean_entry_bonus"] if clean else 0.0
    c["entry_coverage"] = round(
        WEIGHTS["entry_coverage_bonus"] * f.get("entry_coverage", 0.0), 2
    )
    if not f.get("global_count"):
        c["purity"] = WEIGHTS["pure_bonus"]
    elif not f.get("globals_written"):
        c["purity"] = WEIGHTS["read_only_bonus"]
    else:
        c["purity"] = 0.0
    c["computational"] = WEIGHTS["computational_bonus"] * min(
        1.0, computational_density / 0.5
    )
    c["arity"] = min(
        MAX_ARITY_BONUS,
        WEIGHTS["arity_bonus_per_entry"] * len(f.get("clean_extrinsic_entries", ())),
    )
    loc = f.get("lines_code", 0)
    over = max(0, loc - IDEAL_LINES[1]) + max(0, IDEAL_LINES[0] - loc)
    c["size"] = WEIGHTS["size_penalty_per_line"] * over
    return {
        "score": round(sum(c.values()), 2),
        "components": {k: round(v, 2) for k, v in c.items()},
    }


def load_entrypoints(path: Path | None) -> dict[str, set[str]]:
    """Load RPC / option / protocol routine sets dumped from the container."""
    keys = ("rpc", "option", "protocol", "install")
    if path is None or not path.exists():
        return {k: set() for k in keys}
    raw = json.loads(path.read_text())
    return {
        "rpc": {s.upper() for s in raw.get("rpc_routines", [])},
        "option": {s.upper() for s in raw.get("option_routines", [])},
        "protocol": {s.upper() for s in raw.get("protocol_routines", [])},
        "install": {s.upper() for s in raw.get("install_routines", [])},
    }


def rank_routines(
    facts: dict[str, dict[str, Any]],
    source_dir: Path,
    entrypoints: dict[str, set[str]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Score every analysed routine; return (ranked eligible, exclusion tally)."""
    ranked: list[dict[str, Any]] = []
    tally: dict[str, int] = {}
    for name, f in facts.items():
        if "analysis_error" in f:
            tally["analysis_error"] = tally.get("analysis_error", 0) + 1
            continue
        excl = _exclusions(f, entrypoints)
        for e in excl:
            tally[e] = tally.get(e, 0) + 1
        if excl:
            continue
        text = (source_dir / f["path"]).read_text(encoding="utf-8", errors="replace")
        dens = _computational_density(text)
        s = score_routine(f, dens)
        ranked.append(
            {
                "name": name,
                "file": f["path"],
                "score": s["score"],
                "components": s["components"],
                "computational_density": round(dens, 3),
                "fanout": f["fanout"],
                "fanin": f.get("fanin", 0),
                "transitive_calls": f.get("transitive_calls", 0),
                "transitive_global_count": f.get("transitive_global_count", 0),
                "global_count": f["global_count"],
                "globals_read": f["globals_read"],
                "globals_written": f["globals_written"],
                "scratch_globals": f["scratch_globals"],
                "naked_read": f["naked_read"],
                "naked_write": f["naked_write"],
                "lines_total": f["lines_total"],
                "lines_code": f["lines_code"],
                "label_count": f["label_count"],
                "clean_extrinsic_entries": f["clean_extrinsic_entries"],
                "entry_coverage": f["entry_coverage"],
                "reachable_code_lines": f["reachable_code_lines"],
                "labels": [
                    {"label": l["label"], "formals": l["formals"], "clean": l["clean"]}
                    for l in f["labels"]
                ],
                "calls": f["calls"],
                "entryrefs": f["entryrefs"],
                "commands": f["commands"],
                "indirection": f["indirection"],
                "xecute": f["xecute"],
                "description": f.get("description", ""),
            }
        )
    ranked.sort(key=lambda r: (-r["score"], r["name"]))
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    return ranked, dict(sorted(tally.items(), key=lambda kv: -kv[1]))


DUP_THRESHOLD = 0.6


def _code_shingles(text: str) -> set[str]:
    """Normalised set of executable lines, for near-duplicate comparison."""
    out: set[str] = set()
    for line in text.splitlines():
        if line[:1] not in (" ", "\t"):
            continue
        body = strip_comment(mask_strings(line)).strip()
        if body:
            out.add(re.sub(r"\s+", " ", body))
    return out


def find_duplicate_clusters(
    ranked: Sequence[dict[str, Any]],
    source_dir: Path,
    threshold: float = DUP_THRESHOLD,
    min_lines: int = 8,
) -> list[list[str]]:
    """Group near-identical routines that would leak across the train/eval split.

    VistA carries the same algorithm under several namespaces — ``GMTSUMX3`` and
    ``SROGMTS2`` are byte-identical after comment stripping. PROJECT.md section 9
    says to split *routines*, not tasks, but that is only leak-proof if no two
    routines are copies of one another. Callers should keep each returned cluster
    wholly on one side of the split, or drop all but one member.

    Returns clusters of two or more routine names, largest first. Comparison is
    Jaccard similarity over normalised executable lines, bucketed through an
    inverted index so the cost stays near-linear.
    """
    import itertools
    from collections import Counter, defaultdict

    sig: dict[str, set[str]] = {}
    for r in ranked:
        lines = _code_shingles(
            (source_dir / r["file"]).read_text(encoding="utf-8", errors="replace")
        )
        if len(lines) >= min_lines:
            sig[r["name"]] = lines

    inverted: dict[str, list[str]] = defaultdict(list)
    for name, lines in sig.items():
        for line in lines:
            if len(line) > 25:  # rare-enough lines make useful buckets
                inverted[line].append(name)

    seen_pairs: Counter[tuple[str, str]] = Counter()
    for names in inverted.values():
        if 1 < len(names) <= 25:
            for pair in itertools.combinations(sorted(names), 2):
                seen_pairs[pair] += 1

    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (a, b) in seen_pairs:
        inter = len(sig[a] & sig[b])
        union = len(sig[a] | sig[b])
        if union and inter / union >= threshold:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

    groups: dict[str, list[str]] = defaultdict(list)
    for x in list(parent):
        groups[find(x)].append(x)
    return sorted(
        (sorted(v) for v in groups.values() if len(v) > 1),
        key=lambda v: (-len(v), v[0]),
    )


def build_report(
    ranked: Sequence[dict[str, Any]],
    tally: dict[str, int],
    facts: dict[str, dict[str, Any]],
    source_dir: Path | str,
    top: int,
    duplicate_clusters: Sequence[Sequence[str]] = (),
) -> dict[str, Any]:
    """Assemble the auditable candidates document."""
    return {
        "schema": "rosetta.bench.select/candidates/1",
        "generator": "rosetta/bench/select.py",
        "spec": "docs/PROJECT.md section 9 — Routine selection",
        "source_dir": str(source_dir),
        "reproduce": {
            "note": (
                "Ranking needs the WHOLE routine corpus, not just the emitted "
                "candidates: fan-in and 3-hop transitive reach are corpus-wide. "
                "Running against data/routines alone silently inflates routines "
                "whose callees are absent (AJETIU4 rises from rank 19 to rank 1 "
                "because VADPT is not in the subset). Extract the full corpus "
                "first."
            ),
            "commands": [
                "python -m rosetta.bench.select --extract --source-dir /tmp/vista-routines",
                "python -m rosetta.bench.select --source-dir /tmp/vista-routines"
                " --out data/tasks/candidates.json",
            ],
            "container_routine_dir": "vehu:/home/vehu/r",
        },
        "corpus": {
            "routines_analysed": len(facts),
            "eligible_after_exclusions": len(ranked),
            "emitted": min(top, len(ranked)),
        },
        "scoring": {
            "note": (
                "Additive; higher is a more tractable mutation-repair target. "
                "Every component is emitted per routine so the ranking can be "
                "recomputed by hand. Exclusions are hard filters applied before "
                "scoring."
            ),
            "weights": WEIGHTS,
            "ideal_code_lines": list(IDEAL_LINES),
        },
        "exclusion_tally": tally,
        "duplicate_clusters": {
            "note": (
                "Near-identical routines (Jaccard >= "
                f"{DUP_THRESHOLD} over normalised executable lines). PROJECT.md "
                "section 9 splits routines, not tasks — but that only prevents "
                "leakage if no two routines are copies. Keep each cluster wholly "
                "on one side of the train/eval split, or keep one member and drop "
                "the rest."
            ),
            "cluster_count": len(duplicate_clusters),
            "routines_affected": sum(len(c) for c in duplicate_clusters),
            "clusters": [list(c) for c in duplicate_clusters],
        },
        "candidates": list(ranked[:top]),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point. Returns a process exit code."""
    root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description="Rank VistA routines by tractability.")
    ap.add_argument("--source-dir", type=Path, default=root / "data" / "routines")
    ap.add_argument(
        "--out", type=Path, default=root / "data" / "tasks" / "candidates.json"
    )
    ap.add_argument(
        "--entrypoints",
        type=Path,
        default=root / "data" / "tasks" / "vista_entrypoints.json",
    )
    ap.add_argument("--top", type=int, default=500)
    ap.add_argument("--limit", type=int, default=None, help="first N files only")
    ap.add_argument(
        "--facts-out", type=Path, default=None, help="full per-routine facts as JSONL"
    )
    ap.add_argument(
        "--extract", action="store_true", help="pull .m source from the container first"
    )
    ap.add_argument("--container", default="vehu")
    ap.add_argument(
        "--source-label",
        default=None,
        help="what to record as source_dir (use when analysing a scratch extract)",
    )
    args = ap.parse_args(argv)

    if args.extract:
        n = extract_from_container(args.source_dir, container=args.container)
        print(f"extracted {n} routines to {args.source_dir}", file=sys.stderr)

    if not args.source_dir.is_dir():
        print(f"no such source dir: {args.source_dir}", file=sys.stderr)
        return 2

    facts = analyse_directory(args.source_dir, limit=args.limit)
    add_call_graph(facts)
    ranked, tally = rank_routines(facts, args.source_dir, load_entrypoints(args.entrypoints))

    if args.facts_out:
        args.facts_out.parent.mkdir(parents=True, exist_ok=True)
        with args.facts_out.open("w") as fh:
            for name in sorted(facts):
                fh.write(json.dumps(facts[name]) + "\n")

    clusters = find_duplicate_clusters(ranked, args.source_dir)
    member_of = {n: i for i, c in enumerate(clusters) for n in c}
    for r in ranked:
        cid = member_of.get(r["name"])
        r["duplicate_cluster"] = cid
        r["duplicate_siblings"] = (
            [n for n in clusters[cid] if n != r["name"]] if cid is not None else []
        )

    report = build_report(
        ranked,
        tally,
        facts,
        args.source_label or args.source_dir,
        args.top,
        clusters,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1))
    print(
        f"analysed {len(facts)} routines, {len(ranked)} eligible, "
        f"wrote top {report['corpus']['emitted']} to {args.out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
