"""The eight mutation operators from docs/PROJECT.md section 6.

Each operator is a generator over a parsed :class:`~rosetta.mutate.lex.Routine`
that yields :class:`Mutation` records. An operator never executes anything and
never decides whether a mutant is *useful* -- that is the admission pipeline's
job (see :mod:`rosetta.mutate.generate`). An operator's only contract is:

1. The edit lands outside string literals and comments, at a position the
   lexer positively identified. No regex sweeps over raw source.
2. ``mutated_src`` is syntactically well-formed enough to load. A mutant that
   fails to compile tests the wrong thing (PROJECT.md section 9, rule 2).
3. ``mutated_src`` never contains a command-position ``TSTART``/``TCOMMIT``/
   ``TROLLBACK``. None of these operators can introduce a command verb, and
   :func:`~rosetta.mutate.lex.assert_no_tp_command` re-checks every mutant
   anyway.
4. The mutant is a *repair task*, not a broken program. Three ways a mutant
   fails that even though it compiles, all of them found by reconstructing and
   compiling a sample of generated mutants against YottaDB r2.06:

   * it **hangs** -- an unconditional back edge, or a loop whose advance or
     exit condition was deleted. The harness has no defence: a hang is a
     timeout, and PROJECT.md section 9 rates a bare timeout a weak signal.
   * it makes code **permanently dead** -- an unconditional ``QUIT`` or
     ``GOTO`` ahead of a live block. The "bug" is then unreachable code, which
     is not the defect class the benchmark claims to measure.
   * it is a **no-op** -- ``$E(x,0,4)`` is ``$E(x,1,4)``, ``^GLO(a,b)`` ->
     ``^(b)`` after ``^GLO(a,c)`` is byte-identical, ``$G(X,d)`` -> ``$G(X)``
     where ``X`` is provably defined. Unkillable, so unscoreable.

   The guards for these live in the "Structural analysis" section below and are
   enforced at generation time rather than filtered afterwards, so the survey
   count and the admitted count mean the same thing.

Difficulty classes are assigned per sub-operator so scores can be broken out.
The rubric, applied uniformly:

``easy``
    Diverges on nearly every input *and* the edit is locally conspicuous --
    a clause vanished, or a ``'`` negation appeared out of nowhere.
``medium``
    Diverges on a substantial but not universal share of inputs, or the edit
    is plausible-looking but structurally obvious once located.
``hard``
    Diverges only on boundary or rare inputs, or the edit is a single
    character that reads as entirely idiomatic in context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Iterator, Literal

# `_IDENT_RE` and `_set_targets` are private to lex.py, but they are the
# identifier grammar and the SET-target parser this module would otherwise
# duplicate; importing keeps one implementation.
from rosetta.mutate.lex import (
    CallSite,
    Command,
    MLine,
    Routine,
    declared_locals,
    find_call_spans,
    identifier_occurrences,
    matching_paren,
    replace_line,
    splice,
    split_top,
    split_top_spans,
    _IDENT_RE,
    _set_targets,
)

Difficulty = Literal["easy", "medium", "hard"]

__all__ = [
    "Difficulty",
    "Mutation",
    "OPERATORS",
    "OPERATOR_NAMES",
    "mutate_routine",
    "cmp_flip",
    "boundary",
    "stmt_drop",
    "var_swap",
    "naked_ref",
    "dollar_misuse",
    "postcond",
    "arg_order",
]


@dataclass(frozen=True)
class Mutation:
    """One candidate mutation, already applied to produce ``mutated_src``.

    ``detail`` names the sub-operator (``"CMP_FLIP/gt_to_lt"``-style suffix) so
    the results table can drill below the eight headline classes.
    ``timeout_risk`` marks mutations that plausibly turn a bounded loop
    unbounded; PROJECT.md section 9 rule 3 treats a bare timeout as a weak
    divergence signal, so these are tracked separately rather than trusted.
    """

    operator: str
    detail: str
    difficulty: Difficulty
    lineno: int
    label: str | None
    original_line: str
    mutated_line: str | None  # None when the whole line was deleted
    mutated_src: str
    timeout_risk: bool = False
    note: str = ""

    @property
    def key(self) -> str:
        """Stable identity of this edit within its routine."""
        return f"{self.operator}/{self.detail}@{self.lineno}"


# --------------------------------------------------------------------------
# Shared guards and helpers
# --------------------------------------------------------------------------

#: Commands whose argument list is a device/format expression, not an ordinary
#: value expression. ``!`` means newline and ``?`` means column-tab there, so
#: operator-level mutation would be nonsense.
_FORMAT_ARG_COMMANDS = frozenset(
    {"WRITE", "READ", "OPEN", "USE", "CLOSE", "VIEW", "ZSYSTEM", "ZWRITE"}
)

#: Commands whose arguments are pure value expressions and are safe to mutate.
_VALUE_ARG_COMMANDS = frozenset({"IF", "QUIT", "SET", "XECUTE"})


def _has_indirection(line: MLine) -> bool:
    """``@`` outside literals makes static reasoning unsound -- skip the line."""
    return "@" in line.code


def _skip(line: MLine) -> bool:
    if not line.commands or _has_indirection(line):
        return True
    return any(c.canon == "XECUTE" for c in line.commands)


def _wellformed(raw: str) -> bool:
    """Cheap post-edit sanity check on one line: balanced quotes and parens."""
    from rosetta.mutate.lex import mask_strings

    if raw.count('"') % 2:
        return False
    masked = mask_strings(raw)
    depth = 0
    for ch in masked:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _emit(
    routine: Routine,
    line: MLine,
    new_line: str | None,
    *,
    operator: str,
    detail: str,
    difficulty: Difficulty,
    timeout_risk: bool = False,
    note: str = "",
) -> Mutation | None:
    """Apply an edited line and package it, or return ``None`` if unusable."""
    if new_line is not None:
        if new_line == line.raw or not new_line.strip():
            return None
        if not _wellformed(new_line):
            return None
    src = replace_line(routine.src, line.lineno, new_line)
    return Mutation(
        operator=operator,
        detail=detail,
        difficulty=difficulty,
        lineno=line.lineno,
        label=routine.label_at(line.lineno),
        original_line=line.raw,
        mutated_line=new_line,
        mutated_src=src,
        timeout_risk=timeout_risk,
        note=note,
    )


def _assign_eq_offset(masked: str, start: int, end: int) -> int:
    """Absolute offset of the assignment ``=`` in one ``SET`` argument, or -1."""
    depth = 0
    for i in range(start, end):
        ch = masked[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "=" and depth == 0 and (i == start or masked[i - 1] not in "'<>=["):
            return i
    return -1


def value_spans(line: MLine) -> list[tuple[int, int]]:
    """Regions of ``line`` that are ordinary value expressions.

    Postconditionals (minus the colon), ``IF``/``QUIT`` arguments, ``SET``
    right-hand sides and ``DO``/``GOTO`` actual-parameter lists. Deliberately
    excludes ``WRITE``/``READ``/``OPEN``/``USE`` argument lists, where ``!`` and
    ``?`` are format controls rather than operators.
    """
    out: list[tuple[int, int]] = []
    masked = line.masked
    for cmd in line.commands:
        if cmd.postcond_span is not None:
            ps, pe = cmd.postcond_span
            out.append((ps + 1, pe))
        if cmd.args_span is None:
            continue
        astart, aend = cmd.args_span
        if cmd.canon in _FORMAT_ARG_COMMANDS or cmd.canon in ("NEW", "LOCK", "JOB"):
            continue
        if cmd.canon == "SET":
            for _a, s, e in split_top_spans(masked[astart:aend], ",", base=astart):
                eq = _assign_eq_offset(masked, s, e)
                if eq >= 0:
                    out.append((eq + 1, e))
                else:
                    out.append((s, e))
            continue
        if cmd.canon in ("DO", "GOTO"):
            for site in find_call_spans(line):
                if site.kind == "do" and astart <= site.name_span[0] < aend:
                    o, c = site.paren_span
                    out.append((o + 1, c - 1))
            continue
        if cmd.canon == "FOR":
            # `F I=1:1:10` -- the control variable and its `=` are structure,
            # not an expression. Flipping that `=` produces source that will
            # not load. Only the parameter list after `=` is mutable.
            eq = _assign_eq_offset(masked, astart, aend)
            if eq >= 0:
                out.append((eq + 1, aend))
            continue
        if cmd.canon in _VALUE_ARG_COMMANDS:
            out.append((astart, aend))
    return [(s, e) for s, e in out if e > s]


def _in_spans(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= pos < e for s, e in spans)


# --------------------------------------------------------------------------
# Structural analysis shared by the guards
#
# Six defect classes found by compiling every generated mutant against a real
# YottaDB r2.06 compiler are prevented here rather than filtered afterwards.
# Each guard is small, local and named after the property it protects; see the
# per-operator docstrings for which one applies where.
# --------------------------------------------------------------------------

#: Commands whose argument list names *lock resources*, not glvns. A naked
#: reference is not a legal lock resource name (``L +^(1):5`` is
#: ``%YDB-E-LKNAMEXPECTED``), and the standard does not define whether LOCK
#: updates the naked-reference indicator, so these spans are excluded from
#: both naked-reference targeting and naked-reference dominance.
_LOCK_COMMANDS = frozenset({"LOCK", "ZALLOCATE", "ZDEALLOCATE"})

#: Sentinel: the last global reference reaching a point is not statically
#: knowable (a call intervened, or the nearest reference is conditional).
_BLOCKED = object()

_BYREF_RE = re.compile(r"^\.([%A-Za-z][A-Za-z0-9]*)")


def _cmd_end(cmd: Command) -> int:
    return cmd.args_span[1] if cmd.args_span is not None else cmd.verb_span[1]


def _for_always_iterates(cmd: Command) -> bool:
    """Does this ``FOR`` always execute its body at least once?

    ``F I=1:1`` and ``F I="A","B"`` do; ``F I=a:b:c`` may not, because the
    limit can already be behind the start. Only the first comma-element
    matters -- it decides whether the first iteration happens at all.
    """
    if not cmd.has_args or cmd.args is None:
        return True
    parts = cmd.args.split("=", 1)
    if len(parts) < 2:
        return True
    first = split_top(parts[1], ",")[0]
    return len(split_top(first, ":")) < 3


@lru_cache(maxsize=8192)
def _unconditional_spans(line: MLine) -> tuple[tuple[int, int], ...]:
    """Regions of ``line`` that execute on every path that reaches the line.

    A command-level postconditional gates its own command only, so such a
    command is excluded but the ones after it are not. ``IF``/``ELSE`` gate
    *everything to their right*, though their own argument expression is still
    evaluated -- which is why the loop appends before it breaks. ``QUIT`` and
    ``GOTO`` may leave, and a ``FOR`` that can iterate zero times may skip, so
    both end the guaranteed region too.
    """
    out: list[tuple[int, int]] = []
    for cmd in line.commands:
        if cmd.postcond_span is None:
            out.append((cmd.verb_span[0], _cmd_end(cmd)))
        if cmd.canon in ("IF", "ELSE"):
            break
        if cmd.canon in ("QUIT", "GOTO", "HALT"):
            break
        if cmd.canon == "FOR" and not _for_always_iterates(cmd):
            break
    return tuple(out)


@lru_cache(maxsize=8192)
def _naked_barriers(line: MLine) -> tuple[int, ...]:
    """Offsets on ``line`` after which the naked-reference indicator is unknown.

    Any transfer of control -- an extrinsic ``$$``, a ``DO``/``GOTO``/``JOB``,
    including the argumentless ``DO`` that opens a dot block -- can execute
    arbitrary global references in another frame. So can a ``LOCK``, whose
    effect on the indicator is implementation-defined.
    """
    masked = line.masked
    lo, hi = line.code_span
    out: list[int] = []
    idx = masked.find("$$", lo)
    while 0 <= idx < hi:
        out.append(idx)
        idx = masked.find("$$", idx + 2)
    for cmd in line.commands:
        if cmd.canon in ("DO", "GOTO", "JOB") or cmd.canon in _LOCK_COMMANDS:
            out.append(cmd.verb_span[0])
    return tuple(sorted(out))


@lru_cache(maxsize=8192)
def _for_control_vars(line: MLine) -> frozenset[str]:
    """Control variables of every ``FOR`` with an argument list on ``line``."""
    out: set[str] = set()
    for cmd in line.commands:
        if cmd.canon != "FOR" or not cmd.args:
            continue
        head = split_top(cmd.args, "=")[0].strip()
        if _IDENT_RE.fullmatch(head):
            out.add(head)
    return frozenset(out)


@lru_cache(maxsize=8192)
def _written_names(line: MLine) -> frozenset[str]:
    """Local names ``line`` may write: assignment targets and by-reference actuals.

    Reading a name cannot change a loop's termination condition; writing it
    can. The distinction is what keeps the loop-advance guards from rejecting
    every line that merely mentions the loop variable.
    """
    out: set[str] = set(_for_control_vars(line))
    for cmd in line.commands:
        if cmd.args is None:
            continue
        if cmd.canon == "SET":
            for lhs in _set_targets(cmd.args):
                m = _IDENT_RE.match(lhs)
                if m and not lhs.startswith("^"):
                    out.add(m.group(0))
        elif cmd.canon in ("READ", "MERGE", "KILL"):
            for a in split_top(cmd.args, ","):
                a = a.strip()
                if cmd.canon == "MERGE":
                    a = split_top(a, "=")[0].strip()
                m = _IDENT_RE.match(a)
                if m and not a.startswith("^"):
                    out.add(m.group(0))
    for site in find_call_spans(line):
        for a, _s, _e in site.args:
            m = _BYREF_RE.match(a.strip())
            if m:
                out.add(m.group(1))
    return frozenset(out)


def _cmd_written_names(line: MLine, cmd: Command) -> frozenset[str]:
    """Names one command writes: its own ``SET`` targets and by-reference actuals."""
    out: set[str] = set(_set_target_names(cmd))
    if cmd.args_span is not None:
        lo, hi = cmd.args_span
        for site in find_call_spans(line):
            if not lo <= site.name_span[0] < hi:
                continue
            for a, _s, _e in site.args:
                m = _BYREF_RE.match(a.strip())
                if m:
                    out.add(m.group(1))
    return frozenset(out)


def _preceding_lines(routine: Routine, line: MLine) -> list[MLine]:
    """Executable lines before ``line``, restricted to its label span."""
    label = routine.label_at(line.lineno)
    scope = routine.lines_in(label) if label else routine.lines
    return [ln for ln in scope if ln.lineno < line.lineno and ln.commands]


def _enclosing_openers(routine: Routine, line: MLine) -> list[MLine]:
    """Lines that open the dot blocks containing ``line``, outermost last."""
    out: list[MLine] = []
    cur = line.dot_level
    if cur == 0:
        return out
    for ln in reversed(_preceding_lines(routine, line)):
        if ln.dot_level < cur:
            cur = ln.dot_level
            out.append(ln)
            if cur == 0:
                break
    return out


def _code_follows_in_block(routine: Routine, line: MLine) -> bool:
    """Is there executable code after ``line`` inside the same dot block?

    Used to tell a postconditional whose removal kills a routine tail from one
    whose removal is merely a value change at the end of a block.
    """
    code = _code_lines(routine)
    try:
        i = next(k for k, ln in enumerate(code) if ln.lineno == line.lineno)
    except StopIteration:  # pragma: no cover - line has no commands
        return False
    for nxt in code[i + 1:]:
        if nxt.dot_level < line.dot_level:
            return False
        if line.dot_level == 0 and nxt.label is not None:
            return False
        return True
    return False


def _for_before(line: MLine, pos: int) -> Command | None:
    """The innermost ``FOR`` on ``line`` whose scope covers offset ``pos``."""
    best: Command | None = None
    for cmd in line.commands:
        if cmd.canon == "FOR" and cmd.verb_span[0] < pos:
            best = cmd
    return best


def _commands_after(line: MLine, pos: int) -> list[Command]:
    return [c for c in line.commands if c.verb_span[0] > pos]


# --- loop-advance protection (STMT_DROP) ---------------------------------


@lru_cache(maxsize=8192)
def _loop_guard_vars(line: MLine) -> frozenset[str]:
    """Names read by the ``Q:`` that terminates an argumentless ``FOR`` on ``line``.

    ``F  S V=$O(...) Q:cond  D`` has exactly one exit, and ``cond`` reads the
    variable the ``SET`` advances. Anything that stops that variable moving
    turns the loop into an infinite spin or makes the dot block unreachable,
    so these names are protected from statement deletion.
    """
    fors = [c.verb_span[0] for c in line.commands if c.canon == "FOR" and not c.has_args]
    if not fors:
        return frozenset()
    first = min(fors)
    out: set[str] = set()
    for cmd in line.commands:
        if cmd.canon != "QUIT" or cmd.postcond_span is None:
            continue
        if cmd.verb_span[0] < first:
            continue
        ps, pe = cmd.postcond_span
        out |= {o.name for o in identifier_occurrences(line.masked, ps + 1, pe)}
    return frozenset(out)


def _enclosing_loop_guard_vars(routine: Routine, line: MLine) -> frozenset[str]:
    """:func:`_loop_guard_vars` for every unbounded ``FOR`` that encloses ``line``."""
    out: set[str] = set()
    for opener in _enclosing_openers(routine, line):
        out |= _loop_guard_vars(opener)
    return frozenset(out)


_GOTO_TARGET_RE = re.compile(r"^([%A-Za-z][A-Za-z0-9]*)?")


def _backedge_guard_vars(routine: Routine, line: MLine) -> frozenset[str]:
    """Names read by a conditional ``GOTO`` on ``line`` that jumps backwards.

    ``XUA4A71`` spells one of its loops without a ``FOR`` at all::

        C ;Change sound to another
         S E=$P(E,F,1)_T_$P(E,F,2,99) G C:E[F Q

    Delete the ``SET`` and ``E`` never changes, so ``G C:E[F`` becomes an
    unconditional back edge. Same failure as a frozen ``FOR`` advance, so the
    same protection: names the back edge tests are not deletable.
    """
    out: set[str] = set()
    for cmd in line.commands:
        if cmd.canon != "GOTO" or cmd.args_span is None:
            continue
        astart, aend = cmd.args_span
        argtext = line.masked[astart:aend]
        if "^" in argtext:
            continue  # jumps into another routine; not a back edge in this one
        conds: list[tuple[int, int]] = []
        if cmd.postcond_span is not None:
            conds.append((cmd.postcond_span[0] + 1, cmd.postcond_span[1]))
        backward = False
        for _a, s, e in split_top_spans(argtext, ",", base=astart):
            span = _arg_postcond_span(line.masked, s, e)
            head = line.masked[s: span[0] if span else e].strip()
            m = _GOTO_TARGET_RE.match(head)
            tag = (m.group(1) or "") if m else ""
            spans = routine.label_spans.get(tag.upper())
            if not spans or spans[0] > line.lineno:
                continue
            backward = True
            if span is not None:
                conds.append((span[0] + 1, span[1]))
        if not backward:
            continue
        for cs, ce in conds:
            out |= {o.name for o in identifier_occurrences(line.masked, cs, ce)}
    return frozenset(out)


# --- naked-reference context (NAKED_REF) ---------------------------------


def _ref_prefix(line: MLine, name: str, subs: list[tuple[str, int, int]]) -> str:
    """The subscripts a naked reference would inherit from this reference.

    ``^DPT(1,2,0)`` yields ``^DPT(1,2``. Two references with equal prefixes are
    interchangeable as naked-reference context, which makes rewriting one of
    them as ``^(last)`` a textual change with no behavioural effect.
    """
    body = "".join(line.raw[s:e] + "," for _t, s, e in subs[:-1])
    return f"^{name}({body}".replace(" ", "")


def _line_naked_context(line: MLine, limit: int | None) -> object | None:
    """Naked-reference context established by ``line`` before offset ``limit``.

    Returns the prefix of the last global reference, :data:`_BLOCKED` when that
    reference is not statically knowable, or ``None`` when the line establishes
    no context at all.
    """
    events: list[tuple[int, str, object]] = []
    for pos in _naked_barriers(line):
        if limit is None or pos < limit:
            events.append((pos, "call", None))
    for ref in _global_refs(line):
        if limit is None or ref[1] < limit:
            events.append((ref[1], "ref", ref))
    if not events:
        return None
    pos, kind, payload = max(events, key=lambda e: e[0])
    if kind == "call":
        return _BLOCKED
    if not _in_spans(pos, list(_unconditional_spans(line))):
        return _BLOCKED
    name, _caret, _close, subs = payload  # type: ignore[misc]
    return _ref_prefix(line, name, list(subs))


def _naked_context(routine: Routine, line: MLine, before: int) -> object | None:
    """The naked-reference context reaching offset ``before`` on ``line``.

    A prefix string means: on every path reaching this point the most recent
    global reference was that one. :data:`_BLOCKED` means it is not knowable.
    ``None`` means no global reference dominates this point at all -- the naked
    reference would resolve against whatever the *caller* last touched, which
    is not a stable benchmark signal.
    """
    ctx = _line_naked_context(line, before)
    if ctx is not None:
        return ctx
    cur = line.dot_level
    for ln in reversed(_preceding_lines(routine, line)):
        if _has_indirection(ln):
            return _BLOCKED
        if ln.dot_level > cur:
            # A deeper block: it may or may not have run on this path.
            if _global_refs(ln) or _naked_barriers(ln):
                return _BLOCKED
            continue
        limit = None
        if ln.dot_level < cur:
            cur = ln.dot_level
            # This line opens the block we are inside. Its argumentless ``DO``
            # is the entry into that block, not an unrelated call, and nothing
            # after it on the line runs before us -- so scan only up to it.
            entries = [
                c.verb_span[0]
                for c in ln.commands
                if c.canon == "DO" and not c.has_args
            ]
            if entries:
                limit = min(entries)
        ctx = _line_naked_context(ln, limit)
        if ctx is not None:
            return ctx
    return None


# --- reaching definitions (DOLLAR_MISUSE) --------------------------------


def _set_target_names(cmd: Command) -> frozenset[str]:
    """Base local names one ``SET`` command assigns."""
    if cmd.args is None or cmd.canon != "SET":
        return frozenset()
    out: set[str] = set()
    for lhs in _set_targets(cmd.args):
        m = _IDENT_RE.match(lhs)
        if m and not lhs.startswith("^"):
            out.add(m.group(0))
    return frozenset(out)


@lru_cache(maxsize=8192)
def _unconditional_writes(line: MLine) -> frozenset[str]:
    """Names ``line`` assigns on every path through it."""
    spans = list(_unconditional_spans(line))
    out: set[str] = set()
    for cmd in line.commands:
        if _in_spans(cmd.verb_span[0], spans):
            out |= _set_target_names(cmd)
    return frozenset(out)


def _if_else_pair_writes(routine: Routine, line: MLine) -> frozenset[str]:
    """Names written by both halves of an ``I ...`` / ``E  ...`` line pair.

    ``I cond S X=1`` followed by ``E  S X=2`` defines ``X`` on every path even
    though neither line does so unconditionally.
    """
    if not line.commands or line.commands[0].canon != "ELSE":
        return frozenset()
    prev, _nxt = _neighbours(routine, line)
    if prev is None or not prev.commands or prev.commands[0].canon != "IF":
        return frozenset()
    if prev.dot_level != line.dot_level:
        return frozenset()
    return _written_names(prev) & _written_names(line)


def _definitely_defined(routine: Routine, line: MLine, name: str) -> bool:
    """Is local ``name`` assigned on every path reaching ``line``?

    Deliberately incomplete and deliberately one-sided: a formal argument does
    *not* count (the caller may omit it), and only dominating writes count. A
    false negative costs one skipped mutant; a false positive would admit an
    unkillable one.
    """
    cur = line.dot_level
    for ln in reversed(_preceding_lines(routine, line)):
        if ln.dot_level > cur:
            continue
        if ln.dot_level < cur:
            cur = ln.dot_level
        if name in _unconditional_writes(ln):
            return True
        if name in _if_else_pair_writes(routine, ln):
            return True
    return False


# --------------------------------------------------------------------------
# CMP_FLIP
# --------------------------------------------------------------------------

#: Longest-first, so ``'=`` is recognised before ``=`` and ``]]`` before ``]``.
_OPS = ("']]", "'>", "'<", "'=", "'[", "']", "'?", "]]", ">", "<", "=", "[", "]", "?", "&", "!")

#: op -> [(replacement, detail, difficulty)]
_FLIPS: dict[str, list[tuple[str, str, Difficulty]]] = {
    "=": [("'=", "eq_to_ne", "easy")],
    "'=": [("=", "ne_to_eq", "easy")],
    "?": [("'?", "pat_to_npat", "easy")],
    "'?": [("?", "npat_to_pat", "easy")],
    ">": [("<", "gt_to_lt", "medium"), ("'<", "gt_to_ge", "hard")],
    "<": [(">", "lt_to_gt", "medium"), ("'>", "lt_to_le", "hard")],
    "'>": [("'<", "le_to_ge", "medium"), ("<", "le_to_lt", "hard")],
    "'<": [("'>", "ge_to_le", "medium"), (">", "ge_to_gt", "hard")],
    "&": [("!", "and_to_or", "medium")],
    "!": [("&", "or_to_and", "medium")],
    "[": [("]", "contains_to_follows", "hard")],
    "]": [("[", "follows_to_contains", "hard")],
    "]]": [("]", "sortsafter_to_follows", "hard")],
}


def _op_at(masked: str, i: int) -> str | None:
    for op in _OPS:
        if masked.startswith(op, i):
            return op
    return None


def cmp_flip(routine: Routine) -> Iterator[Mutation]:
    """``CMP_FLIP`` -- invert or weaken a relational / logical operator.

    Only operators inside :func:`value_spans` are considered, so a ``!`` in a
    ``WRITE`` format list or a ``?`` column-tab is never touched, and the
    assignment ``=`` of a ``SET`` is structurally excluded (only the
    right-hand side is scanned).
    """
    for line in routine.lines:
        if _skip(line):
            continue
        spans = value_spans(line)
        masked = line.masked
        i = 0
        while i < len(masked):
            if not _in_spans(i, spans):
                i += 1
                continue
            if masked[i] == '"':
                i += 1
                while i < len(masked) and masked[i] != '"':
                    i += 1
                i += 1
                continue
            op = _op_at(masked, i)
            if op is None or op not in _FLIPS:
                i += 1
                continue
            # A '?' that opens a pattern is a comparison; skip the pattern body
            # so its bare code letters are never mistaken for operators.
            skip_to = i + len(op)
            if op.endswith("?"):
                from rosetta.mutate.lex import scan_pattern

                skip_to = max(skip_to, scan_pattern(masked, i + len(op)))
            for repl, detail, diff in _FLIPS[op]:
                new_line = splice(line.raw, i, i + len(op), repl)
                m = _emit(
                    routine,
                    line,
                    new_line,
                    operator="CMP_FLIP",
                    detail=detail,
                    difficulty=diff,
                    note=f"col {i}: {op} -> {repl}",
                )
                if m:
                    yield m
            i = skip_to


# --------------------------------------------------------------------------
# BOUNDARY
# --------------------------------------------------------------------------

_PIECE_RE = re.compile(r"\$(P|PIECE|E|EXTRACT)(?=\()", re.IGNORECASE)
_INT_RE = re.compile(r"^-?\d+$")


def _shift(arg: str, delta: int, *, floor: int = 0) -> str | None:
    """Off-by-one an index argument, literally or by appending ``+1``/``-1``.

    MUMPS evaluates strictly left to right with no operator precedence, so
    appending ``-1`` to any expression is always the intended subtraction.

    ``floor`` rejects a literal result below it. It is 1 for the *start* of a
    ``$EXTRACT``/``$PIECE`` range: both functions clamp a start below 1 up to
    1, so ``$E(x,0,4)`` is byte-for-byte ``$E(x,1,4)`` -- an unkillable mutant,
    verified against YottaDB r2.06. The ``+1`` variant covers that boundary
    already, so nothing is lost by refusing the ``-1``.
    """
    arg = arg.strip()
    if not arg:
        return None
    if _INT_RE.match(arg):
        val = int(arg) + delta
        if val < floor:
            return None
        return str(val)
    return f"{arg}{'+' if delta > 0 else '-'}1"


def boundary(routine: Routine) -> Iterator[Mutation]:
    """``BOUNDARY`` -- off-by-one in a ``$PIECE`` / ``$EXTRACT`` index.

    The classic legacy defect. Shifting the *end* of a range is rated hard:
    it only diverges when the operand is long enough to reach the boundary,
    which is exactly what a thin input suite misses.
    """
    for line in routine.lines:
        if _skip(line):
            continue
        masked = line.masked
        lo, hi = line.code_span
        for m in _PIECE_RE.finditer(masked, lo, hi):
            if m.start() > 0 and masked[m.start() - 1].isalnum():
                continue
            open_idx = m.end()
            close = matching_paren(masked, open_idx)
            if close < 0 or close >= hi:
                continue
            args = split_top_spans(masked[open_idx + 1: close], ",", base=open_idx + 1)
            is_piece = m.group(1).upper() in ("P", "PIECE")
            first_idx = 2 if is_piece else 1
            fn = "$P" if is_piece else "$E"
            for pos in (first_idx, first_idx + 1):
                if pos >= len(args):
                    continue
                arg, s, e = args[pos]
                if not arg.strip():
                    continue
                is_range_end = pos == first_idx + 1
                has_range = len(args) > first_idx + 1
                literal = bool(_INT_RE.match(arg.strip()))
                if is_range_end:
                    diff: Difficulty = "hard"
                    kind = "range_end"
                elif has_range:
                    diff = "medium"
                    kind = "range_start"
                else:
                    diff = "easy" if literal else "medium"
                    kind = "index"
                for delta in (1, -1):
                    new_arg = _shift(
                        line.raw[s:e], delta, floor=1 if kind == "range_start" else 0
                    )
                    if new_arg is None or new_arg == line.raw[s:e].strip():
                        continue
                    new_line = splice(line.raw, s, e, new_arg)
                    mut = _emit(
                        routine,
                        line,
                        new_line,
                        operator="BOUNDARY",
                        detail=f"{fn.lstrip('$').lower()}_{kind}_{'plus' if delta > 0 else 'minus'}1",
                        difficulty=diff,
                        note=f"{fn} arg {pos}: {line.raw[s:e]} -> {new_arg}",
                    )
                    if mut:
                        yield mut


# --------------------------------------------------------------------------
# STMT_DROP
# --------------------------------------------------------------------------


def _code_lines(routine: Routine) -> list[MLine]:
    return [ln for ln in routine.lines if ln.commands]


def _neighbours(routine: Routine, line: MLine) -> tuple[MLine | None, MLine | None]:
    """Previous and next executable lines, in source order."""
    code = _code_lines(routine)
    try:
        i = next(k for k, ln in enumerate(code) if ln.lineno == line.lineno)
    except StopIteration:  # pragma: no cover - line has no commands
        return None, None
    return (code[i - 1] if i else None), (code[i + 1] if i + 1 < len(code) else None)


def _droppable_whole_line(routine: Routine, line: MLine) -> bool:
    """Can this whole line be deleted without orphaning a dot block?

    A line at dot level ``L`` may only go if the following executable line is
    at level ``<= L`` (otherwise this line opened the deeper block) and, for
    ``L > 0``, some adjacent line is at exactly ``L`` (otherwise the enclosing
    argumentless ``DO`` would be left with an empty body, which will not load).
    """
    if line.label is not None or len(line.commands) != 1:
        return False
    cmd = line.commands[0]
    if cmd.canon not in ("SET", "DO") or not cmd.has_args:
        return False
    prev, nxt = _neighbours(routine, line)
    if nxt is None or nxt.dot_level > line.dot_level:
        return False
    if line.dot_level > 0:
        if not (
            (prev is not None and prev.dot_level == line.dot_level)
            or nxt.dot_level == line.dot_level
        ):
            return False
    return True


def _lvalue_key(lhs: str) -> str | None:
    """Normalised identity of one assignment target, or ``None`` for a global.

    Subscripts are part of the identity: ``P(3)`` and ``P(5)`` are different
    nodes, so a later ``S P(5)=0`` does not overwrite ``S P(3)=...``. Comparing
    only base names made the generator claim "target reassigned later" in the
    task record when nothing of the sort had happened.
    """
    lhs = lhs.strip().replace(" ", "")
    if not lhs or lhs.startswith("^"):
        return None
    if not _IDENT_RE.match(lhs):
        return None
    return lhs


def _reassigned_later(routine: Routine, line: MLine, cmd: Command) -> bool:
    """True if every node this SET writes is written again later in scope."""
    if cmd.canon != "SET" or cmd.args is None:
        return False
    targets = {k for k in (_lvalue_key(t) for t in _set_targets(cmd.args)) if k}
    if not targets:
        return False
    label = routine.label_at(line.lineno)
    later = routine.lines_in(label) if label else routine.lines
    written: set[str] = set()
    for ln in later:
        if ln.lineno <= line.lineno:
            continue
        for c in ln.commands:
            if c.canon == "SET" and c.args:
                written |= {
                    k for k in (_lvalue_key(t) for t in _set_targets(c.args)) if k
                }
    return targets <= written


def _drops_a_loop_advance(
    routine: Routine, line: MLine, cmd: Command, *, whole_line: bool
) -> bool:
    """Would removing ``cmd`` freeze the exit condition of an unbounded ``FOR``?

    ``F  S V=$O(...) Q:cond  D`` has one exit and one advance; delete the
    advance and the loop either spins forever or the dot block becomes
    unreachable, and the mutant is a harness hang rather than a repair task.
    Two shapes are refused: the inline ``SET`` that advances a variable named
    in the ``Q:`` on the same argumentless-``FOR`` line, and a whole line
    inside such a loop's dot block that *writes* one of those names (``ECOBMC``
    advances its cursor through ``D METHOD(.CHILD,...)``, by reference).
    """
    written = _cmd_written_names(line, cmd)
    guard = _loop_guard_vars(line)
    if guard and _for_before(line, cmd.verb_span[0]) is not None:
        if written & guard:
            return True
    backedge = _backedge_guard_vars(routine, line)
    if backedge and written & backedge:
        return True
    enclosing = _enclosing_loop_guard_vars(routine, line)
    # Only a whole-line deletion removes everything the line wrote; an inline
    # splice removes one command, so judge it on that command alone.
    affected = _written_names(line) if whole_line else written
    if enclosing and affected & enclosing:
        return True
    return False


def _dropped_text(line: MLine, start: int, end: int) -> str:
    """Raw source of a spliced-out region, for the task record."""
    return line.raw[start:end].strip()


def stmt_drop(routine: Routine) -> Iterator[Mutation]:
    """``STMT_DROP`` -- delete a ``SET`` or ``DO`` statement.

    Two forms: deleting a whole line, and splicing one command out of a
    multi-command line. Both are guarded so the result still loads -- the
    dot-block rules in :func:`_droppable_whole_line`, and for the splice form a
    requirement that another command follows on the same line (so a trailing
    ``FOR`` or ``IF`` is never left with nothing to control) -- and both are
    guarded by :func:`_drops_a_loop_advance` so the result still *terminates*.
    """
    for line in routine.lines:
        if _skip(line):
            continue
        if _droppable_whole_line(routine, line):
            cmd = line.commands[0]
            if _drops_a_loop_advance(routine, line, cmd, whole_line=True):
                pass
            else:
                masked_by_later = _reassigned_later(routine, line, cmd)
                diff: Difficulty = (
                    "hard"
                    if masked_by_later
                    else ("easy" if line.dot_level == 0 else "medium")
                )
                m = _emit(
                    routine,
                    line,
                    None,
                    operator="STMT_DROP",
                    detail=f"line_{cmd.canon.lower()}",
                    difficulty=diff,
                    note="whole line deleted"
                    + (" (same target reassigned later)" if masked_by_later else ""),
                )
                if m:
                    yield m

        if len(line.commands) < 2:
            continue
        for idx, cmd in enumerate(line.commands[:-1]):
            if cmd.canon not in ("SET", "DO") or not cmd.has_args:
                continue
            if _drops_a_loop_advance(routine, line, cmd, whole_line=False):
                continue
            start = cmd.verb_span[0]
            end = cmd.args_span[1]  # type: ignore[index]
            dropped = _dropped_text(line, start, end)
            while end < len(line.raw) and line.raw[end] == " ":
                end += 1
            new_line = splice(line.raw, start, end, "")
            diff = "hard" if _reassigned_later(routine, line, cmd) else "medium"
            m = _emit(
                routine,
                line,
                new_line,
                operator="STMT_DROP",
                detail=f"inline_{cmd.canon.lower()}",
                difficulty=diff,
                note=f"dropped command {idx} of {len(line.commands)}: {dropped}",
            )
            if m:
                yield m


# --------------------------------------------------------------------------
# VAR_SWAP
# --------------------------------------------------------------------------


def _edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _similarity_difficulty(a: str, b: str, namespace: str = "") -> Difficulty:
    """Typo-plausible substitutions are hard; unrelated names are easy to spot.

    ``namespace`` is the prefix every local in scope already shares. VistA
    namespaces its variables (``PRCI``, ``PRCJ``, ``PRCLEN``), so an unadjusted
    prefix test would call every substitution in such a routine "hard". The
    shared prefix carries no distinguishing information and is stripped first.
    """
    if namespace and a.startswith(namespace) and b.startswith(namespace):
        sa, sb = a[len(namespace):], b[len(namespace):]
        if sa and sb:
            a, b = sa, sb
    dist = _edit_distance(a, b)
    shared = len(a) > 2 and len(b) > 2 and a[:3] == b[:3]
    if dist <= 1 or shared:
        return "hard"
    if dist <= 2:
        return "medium"
    return "easy"


def _namespace(pool: set[str]) -> str:
    """The longest prefix shared by every name in ``pool`` (may be empty)."""
    import os

    return os.path.commonprefix(sorted(pool)) if len(pool) > 1 else ""


def var_swap(routine: Routine, max_per_line: int = 3) -> Iterator[Mutation]:
    """``VAR_SWAP`` -- substitute a different same-scope local at a read site.

    Only *read* positions inside :func:`value_spans` are rewritten; assignment
    targets, ``NEW`` lists and ``FOR`` control variables are left alone because
    editing those changes scoping rather than data flow. Candidate replacements
    come from :func:`~rosetta.mutate.lex.declared_locals` for the same label
    span, which is what keeps pattern codes and intrinsic names out.
    """
    for line in routine.lines:
        if _skip(line):
            continue
        label = routine.label_at(line.lineno)
        pool = declared_locals(routine, label)
        if len(pool) < 2:
            continue
        ns = _namespace(pool)
        spans = value_spans(line)
        # Rewriting a name inside a FOR parameter list changes the loop bounds
        # and can make the loop unbounded. Still a legitimate mutation, but the
        # divergence may be nothing but a timeout, which section 9 rates weak.
        for_spans = [
            c.args_span
            for c in line.commands
            if c.canon == "FOR" and c.args_span is not None
        ]
        emitted = 0
        for s, e in spans:
            for occ in identifier_occurrences(line.masked, s, e):
                if occ.name not in pool:
                    continue
                others = sorted(n for n in pool if n != occ.name)
                others.sort(key=lambda n: (_edit_distance(occ.name, n), n))
                for repl in others[:2]:
                    if emitted >= max_per_line:
                        break
                    new_line = splice(line.raw, occ.start, occ.end, repl)
                    m = _emit(
                        routine,
                        line,
                        new_line,
                        operator="VAR_SWAP",
                        detail=f"{occ.name}_to_{repl}",
                        difficulty=_similarity_difficulty(occ.name, repl, ns),
                        timeout_risk=_in_spans(occ.start, for_spans),
                        note=f"col {occ.start}: {occ.name} -> {repl}",
                    )
                    if m:
                        emitted += 1
                        yield m
                if emitted >= max_per_line:
                    break
            if emitted >= max_per_line:
                break


# --------------------------------------------------------------------------
# NAKED_REF
# --------------------------------------------------------------------------

_GLOBAL_RE = re.compile(r"\^([%A-Za-z][A-Za-z0-9]*)?(?=\()")


@lru_cache(maxsize=8192)
def _global_refs(
    line: MLine,
) -> tuple[tuple[str, int, int, tuple[tuple[str, int, int], ...]], ...]:
    """Global references on a line: ``(name, caret_pos, close_pos, subscripts)``.

    ``name`` is empty for a naked reference ``^(...)``. Entry references
    (``$$TAG^ROU``, ``D ^ROU``) are excluded: they are never followed by a
    subscript list in a value position, and ``DO``/``GOTO``/``JOB`` argument
    spans are skipped outright. ``LOCK``-family argument lists are skipped for
    a different reason: those names are lock resources, not glvns, and a naked
    reference is not a legal lock resource name.
    """
    masked = line.masked
    lo, hi = line.code_span
    banned: list[tuple[int, int]] = [
        c.args_span
        for c in line.commands
        if c.args_span is not None
        and (c.canon in ("DO", "GOTO", "JOB") or c.canon in _LOCK_COMMANDS)
    ]
    out = []
    for m in _GLOBAL_RE.finditer(masked, lo, hi):
        caret = m.start()
        if any(s <= caret < e for s, e in banned):
            continue
        if caret > 0 and (masked[caret - 1].isalnum() or masked[caret - 1] == "%"):
            continue  # TAG^ROU
        open_idx = m.end()
        close = matching_paren(masked, open_idx)
        if close < 0 or close >= hi:
            continue
        subs = split_top_spans(masked[open_idx + 1: close], ",", base=open_idx + 1)
        out.append((m.group(1) or "", caret, close, tuple(subs)))
    return tuple(out)


def naked_ref(routine: Routine) -> Iterator[Mutation]:
    """``NAKED_REF`` -- corrupt or introduce a naked global reference.

    A naked reference ``^(3)`` reuses the subscripts of whichever global
    reference executed last, replacing only the final one. Two sub-operators:

    ``perturb``
        Shift the subscript of an existing ``^(n)``.
    ``introduce``
        Rewrite a fully specified ``^GLO(a,b)`` as ``^(b)``. Four preconditions
        must hold; see :func:`_introducible`.
    """
    for line in routine.lines:
        if _skip(line):
            continue
        for name, caret, close, subs in _global_refs(line):
            if not subs:
                continue
            _last, ls, le = subs[-1]
            if name == "":
                for delta in (1, -1):
                    new_sub = _shift(line.raw[ls:le], delta)
                    if new_sub is None:
                        continue
                    new_line = splice(line.raw, ls, le, new_sub)
                    m = _emit(
                        routine,
                        line,
                        new_line,
                        operator="NAKED_REF",
                        detail=f"perturb_{'plus' if delta > 0 else 'minus'}1",
                        difficulty="medium",
                        note=f"naked subscript {line.raw[ls:le]} -> {new_sub}",
                    )
                    if m:
                        yield m
                continue
            ctx = _introducible(routine, line, name, caret, list(subs))
            if ctx is None:
                continue
            new_line = splice(line.raw, caret, close + 1, f"^({line.raw[ls:le]})")
            m = _emit(
                routine,
                line,
                new_line,
                operator="NAKED_REF",
                detail="introduce",
                difficulty="hard",
                note=(
                    f"^{name}(...) -> ^({line.raw[ls:le]}); "
                    f"resolves against {ctx}...)"
                ),
            )
            if m:
                yield m


def _introducible(
    routine: Routine,
    line: MLine,
    name: str,
    caret: int,
    subs: list[tuple[str, int, int]],
) -> str | None:
    """The dominating naked-reference context, or ``None`` if there is none usable.

    A naked reference is only a *benchmark-grade* defect when what it resolves
    to is a property of this routine. Four preconditions, each one a class of
    defect found by compiling the previous generation of mutants:

    1. A global reference must **dominate** the target -- reach it on every
       path. ``FSCXREFO`` line 41 has none, so ``^(120)`` resolves against
       whatever FileMan last touched and the mutant is caller-dependent.
    2. Nothing may **intervene** between that reference and the target: a call
       runs arbitrary code in another frame (``PRSAOTT`` line 137 sits behind
       two extrinsics), and a conditional reference makes the context
       path-dependent.
    3. The dominating reference's **prefix must differ** from the target's, or
       ``^GLO(a,b)`` -> ``^(b)`` is byte-identical at run time and the mutant
       is unkillable (``AJK1UBDD`` 50, ``IBDF10A`` 62).
    4. The discarded subscripts must not mention an enclosing **FOR control
       variable**, or the reference becomes loop-invariant and the loop stops
       terminating (``ZZGENAPT`` 31).
    """
    dropped = {
        o.name
        for _t, s, e in subs[:-1]
        for o in identifier_occurrences(line.masked, s, e)
    }
    if dropped & _for_control_vars(line):
        return None
    if dropped & {
        v for opener in _enclosing_openers(routine, line) for v in _for_control_vars(opener)
    }:
        return None
    ctx = _naked_context(routine, line, caret)
    if ctx is None or ctx is _BLOCKED:
        return None
    assert isinstance(ctx, str)
    if ctx == _ref_prefix(line, name, subs):
        return None
    return ctx


# --------------------------------------------------------------------------
# DOLLAR_MISUSE
# --------------------------------------------------------------------------

_DATA_RE = re.compile(r"\$(D|DATA)(?=\()", re.IGNORECASE)
_GET_RE = re.compile(r"\$(G|GET)(?=\()", re.IGNORECASE)
_ORDER_RE = re.compile(r"\$(O|ORDER)(?=\()", re.IGNORECASE)


def _fn_sites(line: MLine, rx: re.Pattern[str]) -> list[tuple[re.Match[str], int, list[tuple[str, int, int]]]]:
    masked = line.masked
    lo, hi = line.code_span
    out = []
    for m in rx.finditer(masked, lo, hi):
        if m.start() > 0 and masked[m.start() - 1].isalnum():
            continue
        open_idx = m.end()
        close = matching_paren(masked, open_idx)
        if close < 0 or close >= hi:
            continue
        out.append((m, close, split_top_spans(masked[open_idx + 1: close], ",", base=open_idx + 1)))
    return out


_ZERO_LITERAL_RE = re.compile(r"^[+-]?(?:0+(?:\.0*)?|\.0+)$")


def _default_is_observable(
    routine: Routine, line: MLine, var: str, default: str
) -> bool:
    """Can dropping the default from ``$G(var,default)`` change anything?

    Three ways it cannot, all found by inspecting mutants that the compiler
    accepted and the benchmark could never score:

    * ``$G(X,"")`` *is* ``$G(X)``. A literal no-op.
    * ``X`` is assigned on every path that reaches this line, so the default
      is dead code -- ``LRPXAPI`` line 52 sits under an ``I``/``E`` pair that
      both assign ``MAX``.
    * the self-normalising idiom ``S X=...$G(X,0)...`` with a numerically zero
      default. ``$G(X)`` already yields ``""``, which equals ``0`` under every
      numeric and truth-valued operator, so the mutant survives unless a
      string-sensitive consumer happens to follow (``LRPXAPI`` line 67 reaches
      an identical branch either way).
    """
    if default == '""':
        return False
    if not _IDENT_RE.fullmatch(var):
        return True
    if _definitely_defined(routine, line, var):
        return False
    if _ZERO_LITERAL_RE.match(default):
        for cmd in line.commands:
            if cmd.canon == "SET" and cmd.args:
                if any(_lvalue_key(t) == var for t in _set_targets(cmd.args)):
                    return False
    return True


def dollar_misuse(routine: Routine) -> Iterator[Mutation]:
    """``DOLLAR_MISUSE`` -- confuse ``$DATA``/``$GET`` or flip ``$ORDER`` direction.

    ``$D`` returns an existence code (0/1/10/11), ``$G`` returns a value with a
    default -- swapping them is a realistic legacy defect that type-checks
    fine. ``$G(x,default) -> $G(x)`` is rated hard because it only diverges
    when ``x`` is undefined, i.e. only if the input suite includes the
    undefined-local boundary case.
    """
    for line in routine.lines:
        if _skip(line):
            continue
        for m, _close, args in _fn_sites(line, _DATA_RE):
            if len(args) != 1:
                continue
            new_line = splice(line.raw, m.start(), m.end(), "$G")
            mut = _emit(
                routine, line, new_line,
                operator="DOLLAR_MISUSE", detail="data_to_get", difficulty="easy",
                note="$D -> $G",
            )
            if mut:
                yield mut
        for m, close, args in _fn_sites(line, _GET_RE):
            if len(args) == 1:
                new_line = splice(line.raw, m.start(), m.end(), "$D")
                mut = _emit(
                    routine, line, new_line,
                    operator="DOLLAR_MISUSE", detail="get_to_data", difficulty="easy",
                    note="$G -> $D",
                )
                if mut:
                    yield mut
            elif len(args) == 2:
                _a, s, _e = args[1]
                var = line.raw[args[0][1]: args[0][2]].strip()
                default = line.raw[s:_e].strip()
                if not _default_is_observable(routine, line, var, default):
                    continue
                new_line = splice(line.raw, s - 1, close, "")
                mut = _emit(
                    routine, line, new_line,
                    operator="DOLLAR_MISUSE", detail="get_drop_default", difficulty="hard",
                    note=f"dropped $G default {default} for {var}",
                )
                if mut:
                    yield mut
        for m, close, args in _fn_sites(line, _ORDER_RE):
            if len(args) == 1:
                new_line = splice(line.raw, close, close, ",-1")
                mut = _emit(
                    routine, line, new_line,
                    operator="DOLLAR_MISUSE", detail="order_reverse", difficulty="medium",
                    note="$O forward -> reverse",
                )
                if mut:
                    yield mut
            elif len(args) == 2:
                _d, s, e = args[1]
                d = line.raw[s:e].strip()
                flipped = "1" if d.lstrip("+") == "-1" else "-1"
                new_line = splice(line.raw, s, e, flipped)
                mut = _emit(
                    routine, line, new_line,
                    operator="DOLLAR_MISUSE", detail="order_direction_flip",
                    difficulty="medium", note=f"$O direction {d} -> {flipped}",
                )
                if mut:
                    yield mut


# --------------------------------------------------------------------------
# POSTCOND
# --------------------------------------------------------------------------


def _arg_postcond_span(masked: str, start: int, end: int) -> tuple[int, int] | None:
    """Span of a depth-zero ``:condition`` inside one ``DO``/``GOTO`` argument."""
    depth = 0
    for i in range(start, end):
        ch = masked[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == ":" and depth == 0:
            return (i, end)
    return None


def _unbounded_for(routine: Routine, label: str | None) -> bool:
    """Does this scope contain a ``FOR`` with no terminating limit?

    ``F I=1:1`` and argumentless ``F`` loop until an inner ``QUIT`` fires, so
    dropping or inverting that ``QUIT``'s postconditional can hang.
    """
    scope = routine.lines_in(label) if label else routine.lines
    for ln in scope:
        for c in ln.commands:
            if c.canon != "FOR":
                continue
            if not c.has_args:
                return True
            params = (c.args or "").split("=", 1)
            if len(params) < 2 or len(split_top_spans(params[1], ":")) < 3:
                return True
    return False


def _droppable_postcond(routine: Routine, line: MLine, cmd: Command) -> bool:
    """May the postconditional on ``cmd`` be removed without wrecking the CFG?

    Removing a postconditional from a *transfer of control* does not weaken a
    condition, it deletes an edge. Every structurally degenerate POSTCOND
    mutant found by compiling the previous generation was one of these:

    ``GOTO``
        Never. An unconditional ``G`` either creates a back edge -- ``G DOUB:D``
        -> ``G DOUB`` spins forever in ``XUA4A71`` and ``ESPSOUN`` -- or makes
        everything between it and its target permanently dead (``PRCAFN1``,
        ``VPSMRAR4``).
    ``QUIT`` under a ``FOR`` on the same line
        The quit terminates the loop, not the block. Safe only when nothing
        follows it on the line; otherwise the loop body (typically the trailing
        ``D``) never runs at all -- ``IBBACDM`` 16, ``IBCRU4`` 76.
    ``QUIT`` not under a ``FOR``
        Refused when any code follows in the same block, which the unconditional
        quit would render unreachable -- ``YSASCSA`` 27, ``SCTMAPI1`` 37. And
        refused when the quit is argumentless and *nothing* follows, because
        then the drop is a no-op.

    Every other verb, and every ``invert_*`` variant, is untouched: they change
    a condition rather than the shape of the graph.
    """
    if cmd.canon == "GOTO":
        return False
    if cmd.canon != "QUIT":
        return True
    pos = cmd.verb_span[0]
    if _for_before(line, pos) is not None:
        return not _commands_after(line, pos)
    if _commands_after(line, pos):
        return False
    if _code_follows_in_block(routine, line):
        return False
    return cmd.has_args


def postcond(routine: Routine) -> Iterator[Mutation]:
    """``POSTCOND`` -- drop or invert a postconditional.

    Covers both syntactic positions: the command-level ``S:X>3 Y=1`` and the
    argument-level ``D TAG^ROU:$D(X)``. The argument-level form is rated hard
    because it hides at the end of an entry reference and reads as part of the
    call. Mutations to a ``QUIT`` postconditional inside an unbounded ``FOR``
    are marked ``timeout_risk``: they can turn the loop infinite, and
    PROJECT.md section 9 treats a bare timeout as a weak divergence signal.

    Drops that delete a control-flow edge rather than weaken a condition are
    refused outright; see :func:`_droppable_postcond`.
    """
    for line in routine.lines:
        if _skip(line):
            continue
        label = routine.label_at(line.lineno)
        loop_risk = _unbounded_for(routine, label)
        for cmd in line.commands:
            if cmd.postcond_span is not None:
                ps, pe = cmd.postcond_span
                # Notes go into the published task record, so they quote the
                # raw source. `cmd.postcond` comes off the string-masked line,
                # where `:%1["T"` reads as `:%1[""`.
                cond = line.raw[ps + 1: pe]
                risk = loop_risk and cmd.canon in ("QUIT", "GOTO")
                if _droppable_postcond(routine, line, cmd):
                    m = _emit(
                        routine, line, splice(line.raw, ps, pe, ""),
                        operator="POSTCOND", detail=f"drop_{cmd.canon.lower()}",
                        difficulty="medium", timeout_risk=risk,
                        note=f"dropped :{cond} from {cmd.canon}",
                    )
                    if m:
                        yield m
                m = _emit(
                    routine, line, splice(line.raw, ps, pe, f":'({cond})"),
                    operator="POSTCOND", detail=f"invert_{cmd.canon.lower()}",
                    difficulty="easy", timeout_risk=risk,
                    note=f"inverted :{cond}",
                )
                if m:
                    yield m
            if cmd.canon != "DO" or cmd.args_span is None:
                continue  # a GOTO argument postconditional is an edge, not a test
            astart, aend = cmd.args_span
            for _a, s, e in split_top_spans(line.masked[astart:aend], ",", base=astart):
                span = _arg_postcond_span(line.masked, s, e)
                if span is None:
                    continue
                m = _emit(
                    routine, line, splice(line.raw, span[0], span[1], ""),
                    operator="POSTCOND", detail="drop_argument_level",
                    difficulty="hard", timeout_risk=loop_risk,
                    note=f"dropped argument postconditional {line.raw[span[0]:span[1]]}",
                )
                if m:
                    yield m


# --------------------------------------------------------------------------
# ARG_ORDER
# --------------------------------------------------------------------------

#: Multi-argument intrinsics whose argument order is genuinely swappable.
#: Off by default: PROJECT.md scopes ARG_ORDER to call sites, and ``$P``/``$E``
#: index shuffling belongs to BOUNDARY.
SWAPPABLE_INTRINSICS = ("$TR", "$TRANSLATE")


def _arg_difficulty(a: str, b: str) -> Difficulty:
    a, b = a.strip(), b.strip()
    ident = re.compile(r"^[%A-Za-z][A-Za-z0-9]*$")
    if ident.match(a) and ident.match(b):
        return _similarity_difficulty(a, b)
    if a.startswith('"') != b.startswith('"'):
        return "easy"
    return "medium"


def arg_order(routine: Routine, include_intrinsics: bool = False) -> Iterator[Mutation]:
    """``ARG_ORDER`` -- swap two adjacent actual arguments at a call site.

    Covers ``$$TAG^ROU(a,b)`` extrinsics and ``DO``/``GOTO`` calls with a
    parameter list. Pass-by-reference arguments (``.X``) are swapped too but
    tagged in ``note``, since a by-reference/by-value mismatch produces a
    different class of failure.
    """
    intr = SWAPPABLE_INTRINSICS if include_intrinsics else ()
    for line in routine.lines:
        if _skip(line):
            continue
        for site in find_call_spans(line, intrinsics=intr):
            if len(site.args) < 2:
                continue
            for k in range(len(site.args) - 1):
                (a, as_, ae), (b, bs, be) = site.args[k], site.args[k + 1]
                ta, tb = line.raw[as_:ae], line.raw[bs:be]
                if ta.strip() == tb.strip() or not ta.strip() or not tb.strip():
                    continue
                new_line = splice(line.raw, as_, be, f"{tb},{ta}")
                byref = ta.strip().startswith(".") != tb.strip().startswith(".")
                m = _emit(
                    routine, line, new_line,
                    operator="ARG_ORDER",
                    detail=f"{site.kind}_swap_{k}_{k + 1}",
                    difficulty=_arg_difficulty(ta, tb),
                    note=f"{site.name}: ({ta},{tb}) -> ({tb},{ta})"
                    + (" [by-reference mismatch]" if byref else ""),
                )
                if m:
                    yield m


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

OPERATORS: dict[str, Callable[[Routine], Iterator[Mutation]]] = {
    "CMP_FLIP": cmp_flip,
    "BOUNDARY": boundary,
    "STMT_DROP": stmt_drop,
    "VAR_SWAP": var_swap,
    "NAKED_REF": naked_ref,
    "DOLLAR_MISUSE": dollar_misuse,
    "POSTCOND": postcond,
    "ARG_ORDER": arg_order,
}

OPERATOR_NAMES: tuple[str, ...] = tuple(OPERATORS)


def mutate_routine(
    routine: Routine,
    operators: list[str] | None = None,
) -> list[Mutation]:
    """Run every requested operator over ``routine`` and return the mutations.

    Deduplicates on ``(lineno, mutated_src)`` -- different operators can reach
    the same edit -- keeping the first, which follows :data:`OPERATOR_NAMES`
    order. Every mutant is re-checked for TP commands before being returned.
    """
    from rosetta.mutate.lex import assert_no_tp_command

    names = operators or list(OPERATOR_NAMES)
    seen: set[tuple[int, str]] = set()
    out: list[Mutation] = []
    for name in names:
        if name not in OPERATORS:
            raise KeyError(f"unknown mutation operator {name!r}")
        for mut in OPERATORS[name](routine):
            sig = (mut.lineno, mut.mutated_src)
            if sig in seen:
                continue
            seen.add(sig)
            assert_no_tp_command(mut.mutated_src, f"{routine.name} {mut.key}")
            out.append(mut)
    return out
