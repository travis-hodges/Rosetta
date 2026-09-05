"""Build a supervised fine-tuning set from comprehension labels.

Section 6: fine-tune on comprehension, not modification. This emits the
chat-format JSONL the OpenAI fine-tune API accepts.

The train/eval split is enforced here as well as in `labels`, because this is
the last point before data leaves the repository and a leak past this line is
unrecoverable -- the model has already seen it.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from rosetta.bench.split import load_lock
from rosetta.train.labels import QAPair, generate

SYSTEM_PROMPT = (
    "You are a MUMPS (M) expert working on VistA, the US Department of "
    "Veterans Affairs health system. Answer questions about routines, globals "
    "and the FileMan data dictionary precisely and without speculation. "
    "In MUMPS the globals ARE the database: a write to a global is a "
    "persistent side effect, not a variable assignment."
)


@dataclass(frozen=True)
class SFTExample:
    messages: list[dict[str, str]]

    def to_json(self) -> str:
        return json.dumps({"messages": self.messages}, ensure_ascii=False)


def to_example(pair: QAPair, system: str = SYSTEM_PROMPT) -> SFTExample:
    return SFTExample(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": pair.question},
            {"role": "assistant", "content": pair.answer},
        ]
    )


def assert_no_eval_leakage(pairs: Sequence[QAPair]) -> None:
    """Fail loudly if any pair came from an eval-split routine.

    This is the check that makes the split lock mean something at training
    time. Silent leakage would invalidate every number the benchmark produces.
    """
    lock = load_lock()
    held_out = set(lock["eval"])
    offenders = sorted({p.routine for p in pairs if p.routine in held_out})
    if offenders:
        raise RuntimeError(
            f"{len(offenders)} eval-split routines appear in the training set: "
            f"{offenders[:10]}{'...' if len(offenders) > 10 else ''}"
        )


def build(
    pairs: Iterable[QAPair] | None = None,
    shuffle: bool = True,
    seed: int = 20260905,
) -> list[SFTExample]:
    """Produce shuffled SFT examples from train-split comprehension labels."""
    items = list(pairs) if pairs is not None else generate(split="train")
    assert_no_eval_leakage(items)
    examples = [to_example(p) for p in items]
    if shuffle:
        random.Random(seed).shuffle(examples)
    return examples


def write_jsonl(examples: Sequence[SFTExample], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for ex in examples:
            fh.write(ex.to_json() + "\n")
    return len(examples)


def main(argv: list[str] | None = None) -> int:
    import argparse
    from collections import Counter

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("data/train/sft.jsonl"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    pairs = generate(split="train", limit=args.limit)
    examples = build(pairs)
    by_source = Counter(p.source for p in pairs)
    print(f"{len(examples)} SFT examples from {len({p.routine for p in pairs})} routines")
    for src, n in sorted(by_source.items()):
        print(f"  {src:12} {n}")
    print("eval leakage check: PASS")
    if not args.dry_run:
        write_jsonl(examples, args.out)
        print(f"written -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
