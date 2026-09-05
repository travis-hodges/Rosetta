"""Static analysis of MUMPS routines — a thin layer over ``rosetta.bench.select``.

``rosetta.bench.select`` already contains the tested analyser: entry labels and
their formal arguments, ``DO``/``GOTO``/``JOB``/``$$``/``$TEXT`` call targets,
global references split into reads and writes, naked-reference counting, a
string-literal masker and an argumentless-command splitter. Nothing here
re-implements any of that. This module:

* reshapes ``analyse_source`` output into dataclasses the MCP layer can serialise
* adds **local variable** extraction, which ``select`` does not track (it ranks
  routines by *global* coupling, so locals are irrelevant to it)
* walks the corpus-wide call graph to a requested depth

Nothing here executes MUMPS.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field as dc_field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from rosetta.bench.select import (
    COMMANDS,
    add_call_graph,
    analyse_directory,
    analyse_source,
    mask_strings,
    split_top,
    strip_comment,
)

__all__ = [
    "Label",
    "GlobalRefs",
    "LocalVars",
    "ParsedRoutine",
    "GraphNode",
    "GraphEdge",
    "CallGraph",
    "extract_locals",
    "parse_source",
    "corpus_facts",
    "build_call_graph",
]

_LABEL_RE = re.compile(r"^([%A-Za-z][A-Za-z0-9]*)(?:\(([^)]*)\))?")
_CMD_RE = re.compile(r"^([A-Za-z%]+)(:.*)?$", re.DOTALL)

#: A bare name in an expression: not preceded by ``^`` (global) or ``$``
#: (intrinsic), not part of a longer identifier.
_BARE_NAME_RE = re.compile(r"(?<![\^$%A-Za-z0-9.])(%?[A-Za-z][A-Za-z0-9]*)")

#: Commands whose *first* argument list assigns to locals.
_ARG_TAKING = frozenset(
    {
        "BREAK", "CLOSE", "DO", "FOR", "GOTO", "HALT", "IF", "JOB", "KILL",
        "LOCK", "MERGE", "NEW", "OPEN", "QUIT", "READ", "SET", "TSTART",
        "USE", "VIEW", "WRITE", "XECUTE", "ZALLOCATE", "ZDEALLOCATE",
        "ZKILL", "ZSYSTEM", "ZWRITE",
    }
)

#: Names that look like locals but are not. ``U`` is VistA's ``^`` delimiter
#: (``S U="^"``) and is genuinely a local, so it is deliberately absent.
_NOT_LOCALS = frozenset({"IO", "ION", "IOF", "IOM", "IOSL", "IOST", "IOT", "IOXY"})


# --------------------------------------------------------------------------
# Local variable extraction
# --------------------------------------------------------------------------


def _names(expr: str) -> list[str]:
    out: list[str] = []
    for m in _BARE_NAME_RE.finditer(expr):
        name = m.group(1)
        end = m.end()
        # ``TAG^ROU`` -- TAG is an entry label, not a variable.
        if end < len(expr) and expr[end] == "^":
            continue
        out.append(name.upper())
    return out


def _split_set_lhs(arg: str) -> tuple[str, str]:
    """Split one ``SET`` argument at its top-level ``=``. Mirrors select's rule."""
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
            return arg[:i], arg[i + 1:]
    return arg, ""


@dataclass(frozen=True)
class LocalVars:
    """Local (non-``^``) variables used by a routine.

    MUMPS locals have no sigil and no declaration. ``NEW X`` scopes X to the
    current stack frame; without it a local is visible to every routine called
    from here, which is why VistA passes state in bare locals such as ``DFN``
    (the patient record number) and ``U`` (a ``^`` delimiter). A mutation that
    swaps two same-scope locals is invisible to the reader and obvious to the
    verifier.
    """

    formals: list[str] = dc_field(default_factory=list)
    newed: list[str] = dc_field(default_factory=list)
    written: list[str] = dc_field(default_factory=list)
    read: list[str] = dc_field(default_factory=list)
    unscoped: list[str] = dc_field(default_factory=list)

    @property
    def all(self) -> list[str]:
        return sorted(set(self.formals) | set(self.newed) | set(self.written) | set(self.read))


def extract_locals(text: str) -> LocalVars:
    """Extract local variable usage, split by how each name is used.

    ``unscoped`` is the interesting list: names written by this routine but
    never ``NEW``-ed and not formal arguments, i.e. side effects on the caller's
    symbol table.
    """
    formals: set[str] = set()
    newed: set[str] = set()
    written: set[str] = set()
    read: set[str] = set()

    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        masked = mask_strings(line)
        body_start = 0
        if line[:1] not in (" ", "\t"):
            lm = _LABEL_RE.match(masked)
            if lm:
                if lm.group(2) is not None:
                    for p in lm.group(2).split(","):
                        p = p.strip().lstrip(".")
                        if p:
                            formals.add(p.upper())
                body_start = lm.end()
        body = strip_comment(masked[body_start:])
        if not body.strip():
            continue

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
                read.update(_names(tok))
                i += 1
                continue
            if m and m.group(2):
                read.update(_names(m.group(2)[1:]))
            args = ""
            if canon in _ARG_TAKING and i + 1 < n and tokens[i + 1].strip(". \t"):
                args = tokens[i + 1].strip()
                i += 2
            else:
                i += 1
            if not args:
                continue

            if canon == "NEW":
                stripped = args.strip()
                if stripped.startswith("("):  # NEW (A,B) -- exclusive new
                    read.update(_names(stripped))
                else:
                    for a in split_top(args, ","):
                        newed.update(_names(a))
            elif canon == "SET":
                for a in split_top(args, ","):
                    lhs, rhs = _split_set_lhs(a)
                    lhs = lhs.strip()
                    if lhs.startswith("(") and lhs.endswith(")"):
                        for t in split_top(lhs[1:-1], ","):
                            targets = _names(t)
                            written.update(targets[:1])
                            read.update(targets[1:])
                    else:
                        targets = _names(lhs)
                        written.update(targets[:1])
                        read.update(targets[1:])  # subscripts are reads
                    read.update(_names(rhs))
            elif canon == "FOR":
                for a in split_top(args, ","):
                    lhs, rhs = _split_set_lhs(a)
                    targets = _names(lhs)
                    written.update(targets[:1])
                    read.update(targets[1:])
                    read.update(_names(rhs))
            elif canon in ("KILL", "ZKILL"):
                stripped = args.strip()
                if stripped.startswith("("):
                    read.update(_names(stripped))
                else:
                    for a in split_top(args, ","):
                        targets = _names(a)
                        written.update(targets[:1])
                        read.update(targets[1:])
            elif canon in ("READ", "MERGE"):
                for a in split_top(args, ","):
                    lhs, rhs = _split_set_lhs(a)
                    targets = _names(lhs)
                    written.update(targets[:1])
                    read.update(targets[1:])
                    read.update(_names(rhs))
            elif canon in ("DO", "GOTO", "JOB"):
                for a in split_top(args, ","):
                    op = a.find("(")
                    if op >= 0:
                        read.update(_names(a[op:]))
            else:
                read.update(_names(args))

    for s in (formals, newed, written, read):
        s.difference_update(_NOT_LOCALS)
    read.difference_update({"", })
    unscoped = written - newed - formals
    return LocalVars(
        formals=sorted(formals),
        newed=sorted(newed),
        written=sorted(written),
        read=sorted(read - written - newed - formals),
        unscoped=sorted(unscoped),
    )


# --------------------------------------------------------------------------
# parse_routine
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Label:
    """An entry label. ``formals`` non-empty means it is callable as ``$$TAG^ROU(a,b)``."""

    label: str
    formals: list[str]
    arity: int
    flags: list[str] = dc_field(default_factory=list)
    blocked_by: list[str] = dc_field(default_factory=list)
    clean: bool = True


@dataclass(frozen=True)
class GlobalRefs:
    """Global (``^``-prefixed, persistent) references, split by direction.

    ``naked_read`` / ``naked_write`` count *naked references* — ``^(3)``, which
    reuses the subscripts of whatever global reference executed last. They are
    ambiguous by construction and a classic source of real VistA bugs.
    """

    read: list[str] = dc_field(default_factory=list)
    written: list[str] = dc_field(default_factory=list)
    naked_read: int = 0
    naked_write: int = 0
    locks: list[str] = dc_field(default_factory=list)


@dataclass(frozen=True)
class ParsedRoutine:
    """Static facts about one routine. No execution was performed."""

    name: str
    description: str
    lines_total: int
    lines_code: int
    lines_comment: int
    labels: list[Label]
    calls: list[str]
    entryrefs: list[str]
    globals: GlobalRefs
    locals: LocalVars
    commands: dict[str, int]
    flags: list[str]
    nondeterminism: list[str]
    interactive_calls: list[str]
    scratch_globals: list[str]
    indirection: int
    xecute: int
    non_m_syntax: bool

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["locals"]["all"] = self.locals.all
        return d


def parse_source(name: str, text: str) -> ParsedRoutine:
    """Statically analyse routine source. Never executes anything."""
    f = analyse_source(name, text)
    return ParsedRoutine(
        name=f["name"],
        description=f.get("description", ""),
        lines_total=f.get("lines_total", 0),
        lines_code=f.get("lines_code", 0),
        lines_comment=f.get("lines_comment", 0),
        labels=[
            Label(
                label=l["label"],
                formals=l.get("formals", []),
                arity=l.get("arity", 0),
                flags=l.get("flags", []),
                blocked_by=l.get("blocked_by", []),
                clean=l.get("clean", True),
            )
            for l in f.get("labels", [])
        ],
        calls=f.get("calls", []),
        entryrefs=f.get("entryrefs", []),
        globals=GlobalRefs(
            read=["^" + g for g in f.get("globals_read", [])],
            written=["^" + g for g in f.get("globals_written", [])],
            naked_read=f.get("naked_read", 0),
            naked_write=f.get("naked_write", 0),
            locks=["^" + g for g in f.get("locks", [])],
        ),
        locals=extract_locals(text),
        commands=f.get("commands", {}),
        flags=f.get("flags", []),
        nondeterminism=f.get("nondeterminism", []),
        interactive_calls=f.get("interactive_calls", []),
        scratch_globals=f.get("scratch_globals", []),
        indirection=f.get("indirection", 0),
        xecute=f.get("xecute", 0),
        non_m_syntax=bool(f.get("non_m_syntax", False)),
    )


# --------------------------------------------------------------------------
# call_graph
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GraphNode:
    name: str
    depth: int
    in_corpus: bool
    description: str = ""
    fanout: int = 0
    fanin: int = 0
    globals_touched: int = 0


@dataclass(frozen=True)
class GraphEdge:
    caller: str
    callee: str
    entryrefs: list[str] = dc_field(default_factory=list)


@dataclass(frozen=True)
class CallGraph:
    """Outbound call graph rooted at one routine.

    ``unresolved`` lists callees with no source in the corpus — direct fan-out
    understates real dependency, so this matters: ``$$ORANGE^AJETIU4`` has
    fan-out 1 and no globals of its own, but its one callee reaches most of the
    patient record.
    """

    root: str
    depth: int
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    callers: list[str]
    unresolved: list[str]
    truncated: bool
    node_cap: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@lru_cache(maxsize=4)
def corpus_facts(corpus_dir: str) -> dict[str, dict[str, Any]]:
    """Analyse every routine in the corpus once, with corpus-wide graph facts."""
    facts = analyse_directory(Path(corpus_dir))
    add_call_graph(facts)
    return facts


def build_call_graph(
    root: str,
    depth: int,
    facts: dict[str, dict[str, Any]],
    extra_source: tuple[str, str] | None = None,
    node_cap: int = 200,
    resolve_missing: "Callable[[str], str | None] | None" = None,
) -> CallGraph:
    """Breadth-first outbound call graph, bounded by ``depth`` and ``node_cap``.

    ``extra_source`` lets the root be a routine outside the corpus (fetched from
    the container): pass ``(name, source)`` and it is analysed on the fly.

    ``resolve_missing`` is an optional ``name -> source`` lookup used when a
    callee has no source in the corpus. Supplying it lets the walk continue
    into routines outside the benchmark set; without it those callees are
    reported in ``unresolved`` and their own dependencies are not counted.
    """
    root = root.upper()
    local: dict[str, dict[str, Any]] = {}
    if extra_source is not None and root not in facts:
        local[root] = analyse_source(root, extra_source[1])

    def fact(n: str) -> dict[str, Any] | None:
        hit = local.get(n) or facts.get(n)
        if hit is not None or resolve_missing is None or n in _tried:
            return hit
        _tried.add(n)
        try:
            src = resolve_missing(n)
        except Exception:  # a missing routine must not abort the walk
            src = None
        if not src:
            return None
        local[n] = analyse_source(n, src)
        return local[n]

    _tried: set[str] = set()

    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    unresolved: set[str] = set()
    truncated = False

    def add_node(n: str, d: int) -> None:
        if n in nodes:
            return
        f = fact(n)
        if f is None:
            unresolved.add(n)
            nodes[n] = GraphNode(name=n, depth=d, in_corpus=False)
            return
        globs = set(f.get("globals_read", ())) | set(f.get("globals_written", ()))
        nodes[n] = GraphNode(
            name=n,
            depth=d,
            in_corpus=n in facts,
            description=f.get("description", ""),
            fanout=f.get("fanout", 0),
            fanin=f.get("fanin", 0),
            globals_touched=len(globs),
        )

    add_node(root, 0)
    frontier = [root]
    for d in range(1, max(0, depth) + 1):
        nxt: list[str] = []
        for caller in frontier:
            f = fact(caller)
            if f is None:
                continue
            refs_by_callee: dict[str, list[str]] = {}
            for ref in f.get("entryrefs", ()):
                callee = ref.rsplit("^", 1)[-1]
                if callee and callee != caller:
                    refs_by_callee.setdefault(callee, []).append(ref)
            for callee in f.get("calls", ()):
                refs_by_callee.setdefault(callee, [])
            for callee, refs in sorted(refs_by_callee.items()):
                edges.append(GraphEdge(caller=caller, callee=callee, entryrefs=sorted(refs)))
                if callee not in nodes:
                    if len(nodes) >= node_cap:
                        truncated = True
                        continue
                    add_node(callee, d)
                    nxt.append(callee)
        frontier = nxt
        if not frontier:
            break

    callers = sorted(
        n for n, f in facts.items() if root in f.get("calls", ()) and n != root
    )
    return CallGraph(
        root=root,
        depth=depth,
        nodes=sorted(nodes.values(), key=lambda x: (x.depth, x.name)),
        edges=edges,
        callers=callers,
        unresolved=sorted(unresolved),
        truncated=truncated,
        node_cap=node_cap,
    )
