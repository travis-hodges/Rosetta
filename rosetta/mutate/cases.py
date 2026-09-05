"""Input-suite generation: turn a routine entry point into a list of ``ExecSpec``.

Implements the three sources named in docs/PROJECT.md section 9:

(a) **Static analysis** of formal parameters and referenced globals. Every
    formal is profiled by how the routine actually uses it -- ``$E(X``/``$P(X``
    says string, ``X+`` says numeric, ``X?12UN`` gives an exact shape
    constraint, ``X(`` says subscripted array. Pattern constraints are the
    highest-value signal by far: without one, a validator like
    ``$$VALIDUEI^PRCHUEI`` rejects every random input on its first line and no
    case ever reaches the code a mutation touched.

(b) **Sampling real values from populated globals.** STUBBED -- see
    :class:`GlobalSampler`. Only ``rosetta.core`` may touch YottaDB, so this
    stream ships the seam and a JSON-file-backed implementation, not a live
    sampler.

(c) **Boundary values** -- empty string, zero, negative, very long strings,
    undefined locals (modelled as a short actual-argument list), and missing
    subscripts.

Section 9 requires a minimum of 5 cases per task; :func:`build_cases` targets
10 and the admission pipeline discards any task whose cases fail to separate
baseline from mutant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol, Sequence, runtime_checkable

from rosetta.core.interface import ExecSpec
from rosetta.mutate.lex import (
    Routine,
    declared_locals,
    identifier_occurrences,
    matching_paren,
    scan_pattern,
)
from rosetta.mutate.operators import value_spans

__all__ = [
    "ArgProfile",
    "GlobalSampler",
    "NullSampler",
    "JsonGlobalSampler",
    "MIN_CASES",
    "expand_pattern",
    "profile_entry",
    "free_locals",
    "build_cases",
]

#: PROJECT.md section 9: "Minimum 5 cases per task."
MIN_CASES = 5

#: MUMPS strings are limited well above this; 200 exercises long-input paths
#: without risking a MAXSTRLEN error on an older configuration.
_LONG = "X" * 200


# --------------------------------------------------------------------------
# Pattern synthesis
# --------------------------------------------------------------------------

#: Sample characters per MUMPS pattern code, one tuple per code so successive
#: variants of the same pattern produce genuinely different strings.
_CODE_CHARS: dict[str, tuple[str, ...]] = {
    "N": ("1", "7", "4", "9", "2"),
    "U": ("A", "K", "R", "Z", "B"),
    "L": ("a", "k", "r", "z", "b"),
    "A": ("A", "b", "M", "y", "C"),
    "P": (".", "-", "/", ",", "*"),
    "C": ("\x01", "\x02", "\x03", "\x04", "\x05"),
    "E": ("A", "1", ".", "b", "Z"),
}

_ATOM_RE = re.compile(r"(\d*)(?:\.(\d*))?")


def expand_pattern(pattern: str, variant: int = 0) -> str | None:
    """Synthesize a string matching a MUMPS pattern, or ``None`` if unsupported.

    Handles the forms VistA actually writes: ``12UN``, ``3N1"-"4N``, ``1.3N``,
    ``.E``. Alternation groups ``(1N,1U)`` are not expanded -- ``None`` is
    returned rather than guessing, because a wrong guess produces a case that
    silently exercises the reject path.

    ``variant`` selects different filler characters so a suite can contain
    several distinct conforming values.
    """
    i = 0
    n = len(pattern)
    out: list[str] = []
    if not pattern.strip():
        return None
    while i < n:
        m = _ATOM_RE.match(pattern, i)
        assert m is not None
        lo_txt, hi_txt = m.group(1), m.group(2)
        i = m.end()
        if i >= n:
            return None
        if pattern[i] == "(":
            return None  # alternation: refuse to guess
        if lo_txt == "" and hi_txt is None:
            reps = 1
        elif hi_txt is not None:
            lo = int(lo_txt) if lo_txt else 0
            hi = int(hi_txt) if hi_txt else max(lo, 3)
            reps = max(lo, min(hi, lo + 1)) or hi or 1
        else:
            reps = int(lo_txt)
        if pattern[i] == '"':
            j = pattern.find('"', i + 1)
            if j < 0:
                return None
            out.append(pattern[i + 1: j] * reps)
            i = j + 1
            continue
        start = i
        while i < n and pattern[i].upper() in _CODE_CHARS:
            i += 1
        if i == start:
            return None
        codes = pattern[start:i].upper()
        chars = _CODE_CHARS[codes[0]]
        out.append(chars[variant % len(chars)] * reps)
    text = "".join(out)
    return text or None


# --------------------------------------------------------------------------
# Argument profiling
# --------------------------------------------------------------------------


@dataclass
class ArgProfile:
    """How one formal parameter is used inside its entry point."""

    name: str
    kinds: set[str] = field(default_factory=set)
    patterns: list[str] = field(default_factory=list)
    literals: list[str] = field(default_factory=list)
    subscripted: bool = False
    by_reference: bool = False

    @property
    def numeric(self) -> bool:
        return "numeric" in self.kinds and "string" not in self.kinds


_STRING_FNS = ("$E", "$EXTRACT", "$P", "$PIECE", "$L", "$LENGTH", "$TR",
               "$TRANSLATE", "$A", "$ASCII", "$F", "$FIND", "$RE", "$REVERSE")
_NUMERIC_CHARS = "+-*/\\#"


def profile_entry(routine: Routine, label: str) -> list[ArgProfile]:
    """Profile every formal argument of ``label`` from its use in the routine.

    Walks the label's own span plus any labels it reaches via intra-routine
    ``DO``/``GOTO``, because VistA entry points routinely delegate the actual
    work to a following tag.
    """
    formals = routine.formals.get(label, ())
    profiles = {f: ArgProfile(name=f) for f in formals}
    if not profiles:
        return []
    for line in routine.lines_in(label):
        masked = line.masked
        # A name inside a FOR parameter list or a $E/$P index slot is a count,
        # not text -- the strongest numeric signal available statically.
        numeric_zones = [
            c.args_span
            for c in line.commands
            if c.canon == "FOR" and c.args_span is not None
        ] + _index_zones(masked, *line.code_span)
        for s, e in value_spans(line):
            for occ in identifier_occurrences(masked, s, e):
                p = profiles.get(occ.name)
                if p is None:
                    continue
                _classify(p, masked, line.raw, occ.start, occ.end, e)
                if any(zs <= occ.start < ze for zs, ze in numeric_zones):
                    p.kinds.add("numeric")
        for cmd in line.commands:
            if cmd.args_span is None:
                continue
            _classify_calls(profiles, masked, *cmd.args_span)
    return [profiles[f] for f in formals]


_INDEXED_RE = re.compile(r"\$(P|PIECE|E|EXTRACT|J|JUSTIFY)(?=\()", re.IGNORECASE)


def _index_zones(masked: str, lo: int, hi: int) -> list[tuple[int, int]]:
    """Spans of the numeric index arguments of ``$P``/``$E``/``$J`` on a line."""
    from rosetta.mutate.lex import split_top_spans

    zones: list[tuple[int, int]] = []
    for m in _INDEXED_RE.finditer(masked, lo, hi):
        if m.start() > 0 and masked[m.start() - 1].isalnum():
            continue
        open_idx = m.end()
        close = matching_paren(masked, open_idx)
        if close < 0 or close >= hi:
            continue
        args = split_top_spans(masked[open_idx + 1: close], ",", base=open_idx + 1)
        first = 2 if m.group(1).upper() in ("P", "PIECE") else 1
        for pos in (first, first + 1):
            if pos < len(args):
                zones.append((args[pos][1], args[pos][2]))
    return zones


def _classify(
    p: ArgProfile, masked: str, raw: str, start: int, end: int, limit: int
) -> None:
    before = masked[max(0, start - 12): start]
    after = masked[end: min(len(masked), end + 40)]
    # String-literal content survives only in the raw line; the masked line has
    # it replaced by placeholders. Offsets are identical in both.
    after_raw = raw[end: min(len(raw), end + 40)]
    for fn in _STRING_FNS:
        if before.rstrip().upper().endswith(fn + "("):
            p.kinds.add("string")
    if after[:1] == "(":
        p.subscripted = True
        p.kinds.add("array")
    if after[:1] in _NUMERIC_CHARS or before[-1:] in _NUMERIC_CHARS:
        p.kinds.add("numeric")
    if after[:1] in "<>" or after[:2] in ("'>", "'<"):
        p.kinds.add("numeric")
    if after[:1] == "[" or after[:1] == "_" or before[-1:] == "_":
        p.kinds.add("string")
    if after[:1] == "?" or after[:2] == "'?":
        pat_start = end + (1 if after[:1] == "?" else 2)
        pat_end = scan_pattern(masked, pat_start)
        if pat_end > pat_start:
            p.patterns.append(masked[pat_start:pat_end])
            p.kinds.add("string")
    # Literals the routine tests this argument against are exactly the values
    # that steer it down its branches -- `PRCSTR["O"` is why a random 12UN
    # string never exercises the reject path.
    m = re.match(r"(?:'?=|'?\[|'?\])(\"[^\"]*\"|-?[0-9.]+)", after_raw)
    if m:
        lit = m.group(1)
        val = lit[1:-1] if lit.startswith('"') else lit
        if val and val not in p.literals:
            p.literals.append(val)
        if not lit.startswith('"'):
            p.kinds.add("numeric")


def _classify_calls(
    profiles: dict[str, ArgProfile], masked: str, start: int, end: int
) -> None:
    """Mark formals that are passed by reference (``.X``) at a call site."""
    for m in re.finditer(r"\.([%A-Za-z][A-Za-z0-9]*)", masked[start:end]):
        p = profiles.get(m.group(1))
        if p is not None:
            p.by_reference = True
            p.kinds.add("array")


def free_locals(routine: Routine, label: str) -> list[str]:
    """Locals read in ``label``'s span that the span never assigns.

    These must be seeded via ``ExecSpec.locals_in`` or the entry raises M6 on
    every case. ``$$DVARS^XLFSTR`` reading ``%VLIST`` is the canonical example.
    """
    in_scope = declared_locals(routine, label)
    everywhere = declared_locals(routine)
    free: list[str] = []
    for line in routine.lines_in(label):
        for s, e in value_spans(line):
            for occ in identifier_occurrences(line.masked, s, e):
                if (
                    occ.name not in in_scope
                    and occ.name in everywhere
                    and occ.name not in free
                ):
                    free.append(occ.name)
    return free


# --------------------------------------------------------------------------
# Global sampling seam (STUB)
# --------------------------------------------------------------------------


@runtime_checkable
class GlobalSampler(Protocol):
    """Source of real subscript/value pairs from a populated VistA database.

    PROJECT.md section 9(b) wants input values sampled from VEHU's synthetic
    patient data. Only ``rosetta.core`` may talk to YottaDB, so this stream
    defines the seam and consumes it; wiring a live implementation is a matter
    of passing a different object to :func:`build_cases`.
    """

    def sample(self, root: str, n: int) -> list[tuple[str, str]]:
        """Return up to ``n`` ``(reference, value)`` pairs under global ``root``."""
        ...


class NullSampler:
    """Default sampler: no container available, so no real values.

    Deliberately returns nothing rather than inventing plausible-looking data.
    A task over a global-reading routine built with this sampler exercises only
    the missing-subscript boundary case, which is honest but thin.
    """

    def sample(self, root: str, n: int) -> list[tuple[str, str]]:
        return []


class JsonGlobalSampler:
    """Sampler backed by a JSON dump ``{"^DPT": [["^DPT(3,0)", "value"], ...]}``.

    Lets the benchmark stream capture globals once from the container and hand
    the file to this generator, with no YottaDB dependency here.
    """

    def __init__(self, data: dict[str, list[list[str]]]) -> None:
        self._data = data

    @classmethod
    def from_path(cls, path: str) -> "JsonGlobalSampler":
        import json
        from pathlib import Path

        return cls(json.loads(Path(path).read_text()))

    def sample(self, root: str, n: int) -> list[tuple[str, str]]:
        rows = self._data.get(root) or self._data.get(root.lstrip("^")) or []
        return [(r[0], r[1]) for r in rows[:n]]


# --------------------------------------------------------------------------
# Case construction
# --------------------------------------------------------------------------


_NUMERIC_POOL = ("1", "7", "42", "0", "-1", "3.5", "99999")
_STRING_POOL = ("ABC", "A^B^C", "hello world", "1")
#: Used when static analysis could not tell text from number. Interleaved so a
#: suite covers both readings rather than guessing wrong ten times in a row.
_MIXED_POOL = ("1", "ABC", "3", "A^B^C", "0")


_LITERAL_RE = re.compile(r'"([^"]{1,40})"|(?<![\w.$])(\d{4,9})(?![\w.])')


def harvest_literals(routine: Routine, limit: int = 4) -> list[str]:
    """Constants written in the routine's own source, as candidate inputs.

    A date routine tests ``%H[",0"`` and compares against ``21608``; a random
    ``"ABC"`` never reaches the branch those guard. Harvesting the routine's
    own literals is a cheap stand-in for the domain knowledge this generator
    deliberately does not have, and is only used for arguments whose type
    static analysis could not pin down.
    """
    seen: list[str] = []
    for line in routine.lines:
        lo, hi = line.code_span
        for m in _LITERAL_RE.finditer(line.raw[lo:hi]):
            val = m.group(1) if m.group(1) is not None else m.group(2)
            if val and val not in seen:
                seen.append(val)
    return seen[:limit]


def _values_for(p: ArgProfile, want: int, extra: Sequence[str] = ()) -> list[str]:
    """Ordered candidate values for one formal: typical first, boundary last."""
    vals: list[str] = []

    def add(v: str) -> None:
        if v not in vals:
            vals.append(v)

    pattern_values = [
        got
        for variant in range(3)
        for pat in p.patterns[:2]
        if (got := expand_pattern(pat, variant))
    ]
    for v in pattern_values:
        add(v)
    for lit in p.literals[:3]:
        add(lit)
        # A conforming value that also carries a tested-for literal reaches the
        # branch the literal guards -- e.g. a 12-char UEI that contains "O".
        if pattern_values and len(lit) < len(pattern_values[0]):
            base = pattern_values[0]
            add(base[: len(base) // 2] + lit + base[len(base) // 2 + len(lit):])
    if p.numeric:
        pool: Sequence[str] = _NUMERIC_POOL
    elif "string" in p.kinds:
        pool = _STRING_POOL
    else:
        # Type unknown: hedge across text and number readings first, then try
        # the routine's own constants. Constants go *after* the generic pool on
        # purpose -- the first value of each pool becomes the base tuple every
        # other case varies from, and a harvested fragment like ",0" makes a
        # nonsense base for an entry it was never meant to be an argument to.
        pool = list(_MIXED_POOL) + [
            v for v in extra if v[:1] not in ",;^=" and len(v) > 1
        ]
    for v in pool:
        add(v)
    # Boundary values, always, in the order section 9 lists them.
    for v in ("", "0", "-1", _LONG):
        add(v)
    return vals[:want] if want < len(vals) else vals


def build_cases(
    routine: Routine,
    label: str,
    *,
    sampler: GlobalSampler | None = None,
    n_cases: int = 10,
    timeout_s: float = 10.0,
) -> list[ExecSpec]:
    """Build an input suite for ``routine``'s ``label`` entry point.

    Returns at least :data:`MIN_CASES` specs when the entry takes arguments.
    One formal is varied per case against a fixed base tuple, so a divergence
    is attributable to a single input; the final cases are all-boundary and
    short-argument-list (undefined local) cases.

    ``sampler`` supplies real global values; the default :class:`NullSampler`
    supplies none. Raises ``ValueError`` for an unknown label.
    """
    if label not in routine.formals:
        raise ValueError(f"{routine.name} has no label {label!r}")
    sampler = sampler or NullSampler()
    profiles = profile_entry(routine, label)
    arity = len(profiles)

    globals_in: dict[str, str] = {}
    for root in _globals_read(routine, label):
        for ref, val in sampler.sample(root, 3):
            globals_in[ref] = val

    locals_in: dict[str, str] = {name: "" for name in free_locals(routine, label)}

    if arity == 0:
        # Argumentless entry: vary the free locals and the seeded globals only.
        specs = [
            ExecSpec(
                routine=routine.name, entry=label, args=[],
                locals_in=dict(locals_in), globals_in=dict(globals_in),
                timeout_s=timeout_s,
            )
        ]
        for filler in ("", "0", "-1", "ABC", _LONG):
            specs.append(
                ExecSpec(
                    routine=routine.name, entry=label, args=[],
                    locals_in={k: filler for k in locals_in},
                    globals_in=dict(globals_in), timeout_s=timeout_s,
                )
            )
        return specs[:n_cases] if n_cases < len(specs) else specs

    harvested = harvest_literals(routine)
    pools = [_values_for(p, n_cases, harvested) for p in profiles]
    base = [pool[0] for pool in pools]

    specs: list[ExecSpec] = []
    seen: set[tuple[str, ...]] = set()

    def push(args: list[str]) -> None:
        key = tuple(args)
        if key in seen:
            return
        seen.add(key)
        specs.append(
            ExecSpec(
                routine=routine.name, entry=label, args=list(args),
                locals_in=dict(locals_in), globals_in=dict(globals_in),
                timeout_s=timeout_s,
            )
        )

    push(base)
    depth = max(len(p) for p in pools)
    for k in range(1, depth):
        for idx in range(arity):
            if k >= len(pools[idx]):
                continue
            args = list(base)
            args[idx] = pools[idx][k]
            push(args)
            if len(specs) >= n_cases - 2:
                break
        if len(specs) >= n_cases - 2:
            break

    # All-boundary case, then the undefined-local case (short argument list).
    push(["" for _ in range(arity)])
    push(base[:-1])
    return specs


def _globals_read(routine: Routine, label: str) -> list[str]:
    """Global roots (``^DPT``) referenced anywhere in ``label``'s span."""
    roots: list[str] = []
    for line in routine.lines_in(label):
        lo, hi = line.code_span
        for m in re.finditer(r"\^([%A-Za-z][A-Za-z0-9]*)", line.masked[lo:hi]):
            pos = lo + m.start()
            if pos > 0 and line.masked[pos - 1].isalnum():
                continue
            root = "^" + m.group(1)
            if root not in roots:
                roots.append(root)
    return roots


def summarise_suite(cases: Sequence[ExecSpec]) -> str:
    """One-line human description of a suite, for reports and logs."""
    return f"{len(cases)} cases, arity {len(cases[0].args) if cases else 0}"
