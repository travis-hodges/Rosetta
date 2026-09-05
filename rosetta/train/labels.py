"""Comprehension QA pairs from static analysis.

Five label sources, per the section 6 table:

    call graph        "What does DPTLK call?" / "Who calls DPTLK?"
    global refs       "Which globals does this routine write?"
    FileMan DD        "What file does ^DPT correspond to? What are its fields?"
    routine structure "What are the entry labels and their arguments?"
    idiom             "Rewrite this naked reference explicitly."

Every answer is derived mechanically, so none of it can be wrong in the way a
model-generated label can be wrong.

LEAKAGE: generation is restricted to the TRAIN side of
`data/tasks/split.lock.json`. Section 9 makes the lock binding on every
generator, and this is one -- comprehension labels drawn from an eval routine
would teach the model the very code it is later scored on.
"""

from __future__ import annotations

import gzip
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterator

from rosetta.bench import select
from rosetta.bench.split import DEFAULT_ROUTINES, REPO_ROOT, load_lock

DEFAULT_DD = REPO_ROOT / "data" / "fileman" / "dd.json.gz"

LabelSource = str  # call_graph | globals | fileman | structure | idiom


@dataclass(frozen=True)
class QAPair:
    """One deterministic comprehension example."""

    question: str
    answer: str
    source: LabelSource
    routine: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def load_dd(path: Path = DEFAULT_DD) -> dict[str, Any]:
    """Load the FileMan data dictionary cache."""
    if not path.exists():
        return {"files": {}}
    return json.loads(gzip.open(path).read())


def root_to_file(dd: dict[str, Any]) -> dict[str, str]:
    """Map a global root like '^DPT(' to its FileMan file number."""
    out: dict[str, str] = {}
    for number, meta in dd.get("files", {}).items():
        root = meta.get("root")
        if root:
            out[root] = number
    return out


def _fmt_list(items: list[str]) -> str:
    if not items:
        return "none"
    return ", ".join(sorted(set(items)))


def _global_root(ref: str) -> str:
    """Normalise an analyser global name to FileMan root form.

    select.analyse_source reports bare names ('DPT', 'A1BF'), while the data
    dictionary keys roots as they appear in M source ('^DPT('). Bridge the two.
    """
    name = ref.strip().lstrip("^")
    name = name.split("(", 1)[0].split(",", 1)[0]
    return f"^{name}(" if name else ""


# --------------------------------------------------------------- generators


def structure_pairs(facts: dict[str, Any]) -> Iterator[QAPair]:
    name = facts["name"]
    labels = facts.get("labels") or []
    if labels:
        entries = []
        for info in labels:
            tag = info.get("label", "")
            args = info.get("formals") or []
            if not tag:
                continue
            entries.append(f"{tag}({', '.join(args)})" if args else tag)
        yield QAPair(
            question=f"What are the entry labels of the MUMPS routine {name}, "
            "and what formal arguments does each take?",
            answer="; ".join(entries),
            source="structure",
            routine=name,
        )


def call_graph_pairs(facts: dict[str, Any], callers: dict[str, set[str]]) -> Iterator[QAPair]:
    name = facts["name"]
    calls = sorted({c for c in (facts.get("calls") or []) if c != name})
    yield QAPair(
        question=f"Which other routines does {name} call?",
        answer=_fmt_list(calls),
        source="call_graph",
        routine=name,
    )
    who = sorted(callers.get(name, set()) - {name})
    if who:
        yield QAPair(
            question=f"Which routines call {name}?",
            answer=_fmt_list(who),
            source="call_graph",
            routine=name,
        )


def global_pairs(facts: dict[str, Any]) -> Iterator[QAPair]:
    name = facts["name"]
    written = sorted(facts.get("globals_written") or [])
    read = sorted(facts.get("globals_read") or [])
    yield QAPair(
        question=f"Which globals does {name} WRITE to? This matters because in "
        "MUMPS the globals are the database, so a write is a persistent side effect.",
        answer=_fmt_list(written),
        source="globals",
        routine=name,
    )
    if read:
        yield QAPair(
            question=f"Which globals does {name} read?",
            answer=_fmt_list(read),
            source="globals",
            routine=name,
        )


def fileman_pairs(
    facts: dict[str, Any], dd: dict[str, Any], roots: dict[str, str], max_fields: int = 12
) -> Iterator[QAPair]:
    """Explain what a global the routine touches actually MEANS."""
    name = facts["name"]
    refs = (facts.get("globals_read") or []) + (facts.get("globals_written") or [])
    seen: set[str] = set()
    for ref in refs:
        root = _global_root(ref)
        if not root:
            continue
        number = roots.get(root) or roots.get(root.rstrip("("))
        if not number or number in seen:
            continue
        seen.add(number)
        meta = dd["files"].get(number) or {}
        fname = meta.get("name") or ""
        if not fname:
            continue
        yield QAPair(
            question=f"The routine {name} references the global {root}. "
            "Which FileMan file is that, and what does it hold?",
            answer=f"{root} is the root of FileMan file #{number}, {fname} "
            f"({meta.get('field_count', '?')} fields).",
            source="fileman",
            routine=name,
        )
        fields = meta.get("fields") or {}
        if fields:
            shown = []
            for fnum, spec in sorted(fields.items(), key=lambda kv: _fieldkey(kv[0]))[:max_fields]:
                label = spec[0] if isinstance(spec, list) and spec else ""
                store = spec[3] if isinstance(spec, list) and len(spec) > 3 else ""
                if label:
                    shown.append(f"#{fnum} {label}" + (f" [{store}]" if store else ""))
            if shown:
                yield QAPair(
                    question=f"Name some fields of FileMan file #{number} ({fname}), "
                    "with their storage locations in node;piece form.",
                    answer="; ".join(shown),
                    source="fileman",
                    routine=name,
                )


def _fieldkey(num: str) -> tuple[int, float | str]:
    try:
        return (0, float(num))
    except ValueError:
        return (1, num)


def split_subscripts(ref: str) -> tuple[str, list[str]]:
    """'^DENT(226,DA,.1)' -> ('^DENT', ['226', 'DA', '.1'])."""
    if "(" not in ref:
        return ref, []
    head, _, rest = ref.partition("(")
    inner = rest[:-1] if rest.endswith(")") else rest
    return head, [a.strip() for a in select.split_top(inner, ",")] if inner else (head, [])


def resolve_naked(previous: str, naked_args: str) -> str | None:
    """Resolve a naked reference against the preceding global reference.

    MUMPS semantics: the naked reference inherits the previous reference's
    name and all of its subscripts EXCEPT the last, then appends its own.
    `^DENT(226,DA,.1)` followed by `^(.1)` therefore resolves to
    `^DENT(226,DA,.1)` -- not to a copy of the previous reference.
    """
    name, subs = split_subscripts(previous)
    if not subs:
        return None
    own = [a.strip() for a in select.split_top(naked_args, ",")] if naked_args else []
    if not own:
        return None
    return f"{name}({','.join(subs[:-1] + own)})"


def _scan_globals(masked: str) -> list[tuple[int, int]]:
    """(start, end) spans of full global references in a masked line."""
    spans: list[tuple[int, int]] = []
    i = 0
    while True:
        i = masked.find("^", i)
        if i < 0:
            return spans
        j = i + 1
        if j < len(masked) and (masked[j].isalpha() or masked[j] == "%"):
            k = j
            while k < len(masked) and (masked[k].isalnum() or masked[k] == "%"):
                k += 1
            end = k
            if k < len(masked) and masked[k] == "(":
                close = select._matching_paren(masked, k)
                if close > 0:
                    end = close + 1
            spans.append((i, end))
            i = end
        else:
            i = j


def idiom_pairs(facts: dict[str, Any], source: str) -> Iterator[QAPair]:
    """Naked references are the classic MUMPS readability trap.

    `^(3)` reuses the PREVIOUS global reference's subscripts, so reading it
    correctly means tracking state across -- and WITHIN -- lines. The answer
    resolves the reference concretely rather than describing the rule, and it
    quotes the unmasked source so string subscripts survive.
    """
    name = facts["name"]
    previous: str | None = None
    for lineno, raw in enumerate(source.splitlines(), start=1):
        masked = select.strip_comment(select.mask_strings(raw))
        events: list[tuple[int, str, str]] = []
        for a, b in _scan_globals(masked):
            events.append((a, "full", raw[a:b]))
        for m in re.finditer(r"\^\(", masked):
            a = m.start()
            close = select._matching_paren(masked, a + 1)
            if close > 0:
                events.append((a, "naked", raw[a + 2:close]))
        events.sort()
        for _, kind, text in events:
            if kind == "full":
                previous = text
                continue
            if not previous:
                continue
            resolved = resolve_naked(previous, text)
            if not resolved:
                continue
            yield QAPair(
                question="In MUMPS a naked reference inherits the previous global "
                "reference's name and all but its last subscript, then appends its "
                f"own. On line {lineno} of {name}:\n    {raw.strip()}\n"
                f"The preceding global reference is {previous}. "
                f"What does ^({text}) resolve to?",
                answer=resolved,
                source="idiom",
                routine=name,
            )
            previous = resolved


# ------------------------------------------------------------------ driver


def generate(
    routines_dir: Path = DEFAULT_ROUTINES,
    dd_path: Path = DEFAULT_DD,
    split: str = "train",
    limit: int | None = None,
) -> list[QAPair]:
    """Generate comprehension pairs for one side of the locked split.

    `split="train"` is the only value that should ever feed a fine-tune.
    """
    lock = load_lock()
    if split not in ("train", "eval"):
        raise ValueError(f"split must be 'train' or 'eval', got {split!r}")
    names = list(lock[split])
    if limit is not None:
        names = names[:limit]
    wanted = set(names)

    facts_by_name: dict[str, dict[str, Any]] = {}
    sources: dict[str, str] = {}
    for name in names:
        path = routines_dir / f"{name}.m"
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        sources[name] = text
        facts_by_name[name] = select.analyse_source(name, text)

    # Fan-in is only meaningful across the pool we actually analysed.
    callers: dict[str, set[str]] = {}
    for name, facts in facts_by_name.items():
        for callee in facts.get("calls") or []:
            if callee in wanted:
                callers.setdefault(callee, set()).add(name)

    dd = load_dd(dd_path)
    roots = root_to_file(dd)

    pairs: list[QAPair] = []
    for name, facts in facts_by_name.items():
        pairs.extend(structure_pairs(facts))
        pairs.extend(call_graph_pairs(facts, callers))
        pairs.extend(global_pairs(facts))
        pairs.extend(fileman_pairs(facts, dd, roots))
        pairs.extend(idiom_pairs(facts, sources[name]))
    return pairs


def main(argv: list[str] | None = None) -> int:
    import argparse
    from collections import Counter

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--split", default="train", choices=["train", "eval"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    pairs = generate(split=args.split, limit=args.limit)
    by_source = Counter(p.source for p in pairs)
    print(f"{len(pairs)} pairs from the {args.split} split")
    for src, n in sorted(by_source.items()):
        print(f"  {src:12} {n}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w") as fh:
            for p in pairs:
                fh.write(json.dumps(p.to_dict()) + "\n")
        print(f"written -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
