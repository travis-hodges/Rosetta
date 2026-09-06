"""Static facts about a routine that the verifier needs before it executes one.

Three jobs:

1. Reject candidate source that would break the verifier's own TP frame.
2. Decide whether an entry is an extrinsic (``$$TAG^ROU``) or a subroutine.
3. Plan globals_out capture: which global roots to watch, and with which tier.

The parsing is delegated to :mod:`rosetta.bench.select`, which already masks
string literals and canonicalises command names. Stream A does not own that
module and does not modify it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..bench.select import analyse_source, mask_strings, strip_comment
from .config import RESERVED_GLOBALS

#: Commands that would collapse or commit the verifier's own transaction frame.
FORBIDDEN_COMMANDS = ("TSTART", "TCOMMIT", "TROLLBACK", "TRESTART")

#: Command position: start of line, then a label, then whitespace, then any
#: number of dot-nesting markers. Abbreviations included -- `TC` commits too.
_TP_COMMAND_RE = re.compile(
    r"(?:^|[ \t.])(TS|TC|TRO|TRE|TSTART|TCOMMIT|TROLLBACK|TRESTART)"
    r"(?=[ \t:,]|$)",
    re.IGNORECASE,
)

_LABEL_START_RE = re.compile(r"^([%A-Za-z][A-Za-z0-9]*)")

#: A QUIT with a value: `Q X`, `QUIT $$F(1)`, `Q:cond X`. One space then a
#: non-space. `Q  S ...` (two spaces) and a trailing `Q` are argumentless.
_ARGUMENTED_QUIT_RE = re.compile(r"(?:^|[ \t.])Q(?:UIT)?(?::[^ \t]+)? [^ \t]")

#: Roots small enough that a scoped $QUERY walk is cheap even when the static
#: analyser gives us only a bare global name. Anything else is measured.
SCRATCH_ROOTS = frozenset({"^TMP", "^UTILITY", "^XTMP", "^XUTL", "^DISV", "^ROSTMP"})


class RoutineRejected(ValueError):
    """Source that must not be loaded into the verification environment."""


class CaptureTier(str, Enum):
    """How globals_out is collected for one routine."""

    NONE = "none"        # no static writes at all
    QUERY = "query"      # scoped $QUERY walk of named roots
    TRIGGER = "trigger"  # $ZTRIGGER capture; roots too large to walk


@dataclass(frozen=True)
class RoutineFacts:
    """Everything the runtime needs to know statically about one routine."""

    name: str
    source: str
    facts: dict[str, Any]

    @property
    def globals_read(self) -> list[str]:
        return [_root(g) for g in self.facts.get("globals_read", [])]

    @property
    def globals_written(self) -> list[str]:
        return [_root(g) for g in self.facts.get("globals_written", [])]

    @property
    def writes_device(self) -> bool:
        """True if the routine has command-position WRITE or ZWRITE.

        A lower bound: output emitted through ``DO EN^DDIOL`` or through ``@``
        indirection is invisible here. When it is true we pay for a capture
        device; when it is false we skip the device entirely, which is the
        common case for the computational routines the benchmark targets.
        """
        return any(self.facts.get("commands", {}).get(cmd, 0) > 0 for cmd in ("WRITE", "ZWRITE"))

    @property
    def write_set_is_bounded(self) -> bool:
        """False when writes can land somewhere the static analyser cannot name.

        Naked references (``^(3)``), ``@`` indirection and ``XECUTE`` all defeat
        static naming, so no set of watched roots or installed triggers is
        guaranteed complete.
        """
        return (
            self.facts.get("naked_write", 0) == 0
            and self.facts.get("indirection", 0) == 0
            and self.facts.get("xecute", 0) == 0
        )

    @property
    def max_global_depth(self) -> int:
        """Conservative subscript-depth bound over explicit global references."""
        maximum = int(self.facts.get("capture_global_depth", 0))
        for raw in self.source.splitlines():
            text = strip_comment(mask_strings(raw))
            for match in re.finditer(r"\^[%A-Za-z][A-Za-z0-9]*\(", text):
                depth, subscripts = 1, 1
                for char in text[match.end():]:
                    if char == "(":
                        depth += 1
                    elif char == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    elif char == "," and depth == 1:
                        subscripts += 1
                maximum = max(maximum, subscripts)
        return maximum

    def label(self, name: str) -> dict[str, Any] | None:
        for lab in self.facts.get("labels", []):
            if lab.get("label") == name.upper():
                return lab
        return None

    def is_extrinsic(self, entry: str | None) -> bool:
        """Should ``entry`` be invoked as ``$$TAG^ROU(...)``?

        Explicit wins: an entry written ``$$TAG`` is always an extrinsic. Failing
        that, a label with formal arguments whose body has an argumented QUIT is
        one. VistA's convention is consistent enough that this is reliable, and
        being wrong is loud -- YottaDB raises rather than returning silently.
        """
        if entry is None:
            return False
        if entry.startswith("$$"):
            return True
        lab = self.label(entry)
        if lab is None or not lab.get("formals"):
            return False
        return self._label_quits_with_value(entry.upper())

    def _label_quits_with_value(self, label: str) -> bool:
        """True if the label's own body contains an argumented QUIT.

        ``Q`` followed by two spaces is argumentless; ``Q X`` returns X. Scanning
        stops at the next label line, so a later extrinsic in the same routine
        cannot be mistaken for this one.
        """
        in_label = False
        for raw in self.source.splitlines():
            masked = mask_strings(raw)
            starts_label = raw[:1] not in (" ", "\t")
            if starts_label:
                m = _LABEL_START_RE.match(masked)
                if m is None:
                    continue
                if in_label:
                    return False
                in_label = m.group(1).upper() == label
                body = masked[m.end():]
            else:
                if not in_label:
                    continue
                body = masked
            if not in_label:
                continue
            if _ARGUMENTED_QUIT_RE.search(strip_comment(body)):
                return True
        return False


@dataclass(frozen=True)
class CapturePlan:
    """Which globals to watch after a body runs, and how."""

    tier: CaptureTier
    query_roots: tuple[str, ...] = ()
    trigger_roots: tuple[str, ...] = ()
    complete: bool = True
    reason: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def all_roots(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.query_roots + self.trigger_roots))


def _root(name: str) -> str:
    """``select.py`` yields bare names (``DPT``); the runtime wants ``^DPT``."""
    name = name.strip()
    if not name:
        return name
    return name if name.startswith("^") else "^" + name


def parse(name: str, source: str) -> RoutineFacts:
    return RoutineFacts(name=name, source=source, facts=analyse_source(name, source))


def find_tp_commands(source: str) -> list[tuple[int, str]]:
    """Command-position TSTART/TCOMMIT/TROLLBACK/TRESTART, with line numbers.

    Strings are masked and comments stripped first, so ``Q TC`` -- a QUIT whose
    argument happens to be a local named ``TC`` -- is not a hit.
    """
    hits: list[tuple[int, str]] = []
    for lineno, raw in enumerate(source.splitlines(), start=1):
        if not _TP_COMMAND_RE.search(strip_comment(mask_strings(raw))):
            continue
        commands = analyse_source("ROSGUARD", raw + "\n").get("commands", {})
        for command in FORBIDDEN_COMMANDS:
            if commands.get(command, 0):
                hits.append((lineno, command))
    return hits


def reject_if_unsafe(name: str, source: str) -> None:
    """Raise if this source must not enter the verification environment.

    Real VistA never uses transaction processing -- zero occurrences across
    39,612 routines, and zero across the 500 benchmark candidates -- so the
    rejection costs nothing. A mutated or model-generated candidate that emits
    TCOMMIT would commit the verifier's own frame to the live database with no
    error raised at all, which is the worst failure this project can have.
    """
    hits = find_tp_commands(source)
    if hits:
        detail = ", ".join(f"line {ln}: {cmd}" for ln, cmd in hits)
        raise RoutineRejected(
            f"{name}: candidate source contains command-position transaction "
            f"commands ({detail}). Loading it would let the code under test "
            f"commit or destroy the verifier's own TP frame."
        )


def plan_capture(
    facts: RoutineFacts,
    *,
    node_counts: dict[str, int] | None = None,
    query_tier_node_cap: int = 2_000,
    extra_roots: tuple[str, ...] = (),
) -> CapturePlan:
    """Choose the globals_out strategy for one routine.

    ``node_counts`` is an optional measurement of how many nodes already live
    under each root; roots above ``query_tier_node_cap`` are pushed to the
    trigger tier because a full walk of a big production global costs seconds
    (^DPT is 115,882 nodes at ~15us each).
    """
    node_counts = node_counts or {}
    roots = [r for r in dict.fromkeys(list(facts.globals_written) + list(extra_roots))
             if r and r not in RESERVED_GLOBALS]

    notes: list[str] = []
    if not roots:
        if not facts.write_set_is_bounded:
            return CapturePlan(
                tier=CaptureTier.TRIGGER,
                complete=False,
                reason="write set is unbounded (naked write, indirection or XECUTE) "
                       "and no global root could be named statically",
                notes=notes,
            )
        return CapturePlan(tier=CaptureTier.NONE, notes=notes)

    query_roots: list[str] = []
    trigger_roots: list[str] = []
    for r in roots:
        count = node_counts.get(r)
        if r in SCRATCH_ROOTS and count is None:
            query_roots.append(r)
        elif count is not None and count <= query_tier_node_cap:
            query_roots.append(r)
        else:
            trigger_roots.append(r)
            notes.append(
                f"{r}: {'unmeasured' if count is None else f'{count} nodes'} "
                f"-> trigger tier"
            )

    complete = facts.write_set_is_bounded
    reason = "" if complete else (
        "write set is unbounded (naked write, indirection or XECUTE); "
        "captured roots may be incomplete"
    )
    tier = CaptureTier.TRIGGER if trigger_roots else CaptureTier.QUERY
    return CapturePlan(
        tier=tier,
        query_roots=tuple(query_roots),
        trigger_roots=tuple(trigger_roots),
        complete=complete,
        reason=reason,
        notes=notes,
    )
