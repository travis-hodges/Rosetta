"""Train/eval partition and the split lock.

`data/tasks/split.lock.json` is the artifact that answers "did you train on
your eval?" with a file rather than a claim (docs/PROJECT.md section 9). It is
written ONCE, before any task generation, and never rewritten. Every generator
reads it.

The partition is by DUPLICATE CLUSTER, not by routine. Splitting at task level
leaks because two tasks from one routine share structure and identifiers; but
splitting at routine level ALSO leaks, because VistA carries the same algorithm
under several namespaces -- GMTSUMX3 and SROGMTS2 are byte-identical down to a
shared typo. A cluster is therefore the indivisible unit: all of its members
land on the same side.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CANDIDATES = REPO_ROOT / "data" / "tasks" / "candidates.json"
DEFAULT_ROUTINES = REPO_ROOT / "data" / "routines"
DEFAULT_LOCK = REPO_ROOT / "data" / "tasks" / "split.lock.json"

SCHEMA = "rosetta.split.v1"
DEFAULT_EVAL_FRACTION = 0.30
DEFAULT_SEED = 20260905


class SplitLockExists(RuntimeError):
    """Raised rather than overwriting an existing lock. Rule 5 of section 11."""


@dataclass(frozen=True)
class Unit:
    """One indivisible partition unit: a duplicate cluster, or a lone routine."""

    key: str
    routines: tuple[str, ...]
    is_cluster: bool = False

    def __len__(self) -> int:
        return len(self.routines)


@dataclass
class Split:
    train: list[str] = field(default_factory=list)
    eval: list[str] = field(default_factory=list)
    units: list[Unit] = field(default_factory=list)


def source_hash(routine: str, routines_dir: Path) -> str | None:
    """sha256 of a routine's source, or None when it was not extracted."""
    path = routines_dir / f"{routine}.m"
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_units(candidates: dict) -> list[Unit]:
    """Collapse the candidate list into cluster-units.

    Every routine appears in exactly one unit. Cluster membership comes from
    the analyser's `duplicate_clusters`, so the rule is auditable against the
    same evidence that produced the ranking.
    """
    names = [c["name"] for c in candidates["candidates"]]
    known = set(names)
    clusters: list[list[str]] = candidates["duplicate_clusters"]["clusters"]

    assigned: dict[str, int] = {}
    units: list[Unit] = []
    for i, members in enumerate(clusters):
        present = tuple(sorted(m for m in members if m in known))
        if len(present) < 2:
            continue  # not a cluster within this candidate pool
        for m in present:
            assigned[m] = i
        units.append(Unit(key=f"cluster:{i}", routines=present, is_cluster=True))

    for name in sorted(known):
        if name not in assigned:
            units.append(Unit(key=f"routine:{name}", routines=(name,)))
    units.sort(key=lambda u: u.key)
    return units


def partition(
    units: Iterable[Unit],
    eval_fraction: float = DEFAULT_EVAL_FRACTION,
    seed: int = DEFAULT_SEED,
) -> Split:
    """Deterministically assign whole units until the eval quota is filled.

    Units are shuffled with a fixed seed and taken largest-effect-first only in
    the sense that the shuffle is stable; the quota is measured in ROUTINES, not
    units, so a 3-member cluster costs three slots.
    """
    units = sorted(units, key=lambda u: u.key)
    total = sum(len(u) for u in units)
    quota = round(total * eval_fraction)

    order = list(units)
    random.Random(seed).shuffle(order)

    split = Split(units=units)
    taken = 0
    for unit in order:
        if taken + len(unit) <= quota:
            split.eval.extend(unit.routines)
            taken += len(unit)
        else:
            split.train.extend(unit.routines)
    split.train.sort()
    split.eval.sort()
    return split


def content_hash(split: Split, routines_dir: Path) -> str:
    """Hash the split AND the source it refers to.

    Including source digests means the lock detects a corpus that changed
    underneath it, which a list of names alone would not.
    """
    h = hashlib.sha256()
    h.update(SCHEMA.encode())
    for side, names in (("train", split.train), ("eval", split.eval)):
        h.update(side.encode())
        for name in names:
            h.update(name.encode())
            h.update((source_hash(name, routines_dir) or "missing").encode())
    return h.hexdigest()


def build_lock(
    candidates_path: Path = DEFAULT_CANDIDATES,
    routines_dir: Path = DEFAULT_ROUTINES,
    eval_fraction: float = DEFAULT_EVAL_FRACTION,
    seed: int = DEFAULT_SEED,
) -> dict:
    """Produce the lock document without writing it."""
    candidates = json.loads(candidates_path.read_text())
    units = build_units(candidates)
    split = partition(units, eval_fraction, seed)
    clustered = [u for u in units if u.is_cluster]
    return {
        "schema": SCHEMA,
        "note": (
            "Written once, never rewritten. Partitioned by duplicate cluster so "
            "no near-identical routine pair straddles the split. See "
            "docs/PROJECT.md section 9."
        ),
        "seed": seed,
        "eval_fraction": eval_fraction,
        "source": {
            "candidates": str(candidates_path.relative_to(REPO_ROOT)),
            "routines_dir": str(routines_dir.relative_to(REPO_ROOT)),
            "corpus": candidates.get("corpus"),
        },
        "counts": {
            "routines": len(split.train) + len(split.eval),
            "train": len(split.train),
            "eval": len(split.eval),
            "units": len(units),
            "clusters_held_together": len(clustered),
            "routines_in_clusters": sum(len(u) for u in clustered),
        },
        "clusters_held_together": [list(u.routines) for u in clustered],
        "train": split.train,
        "eval": split.eval,
        "content_hash": content_hash(split, routines_dir),
    }


def write_lock(path: Path = DEFAULT_LOCK, force: bool = False, **kwargs: object) -> dict:
    """Write the lock, refusing to clobber an existing one."""
    if path.exists() and not force:
        raise SplitLockExists(
            f"{path} already exists. It is written once and never rewritten -- "
            "it is the proof we did not train on the eval set."
        )
    doc = build_lock(**kwargs)  # type: ignore[arg-type]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n")
    return doc


def load_lock(path: Path = DEFAULT_LOCK) -> dict:
    """Read the lock. Generators call this; they never write."""
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Write the split lock before generating tasks."
        )
    return json.loads(path.read_text())


def verify_lock(path: Path = DEFAULT_LOCK, routines_dir: Path = DEFAULT_ROUTINES) -> bool:
    """True when the recorded hash still matches the source on disk."""
    doc = load_lock(path)
    split = Split(train=doc["train"], eval=doc["eval"])
    return content_hash(split, routines_dir) == doc["content_hash"]


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="write the lock file")
    ap.add_argument("--verify", action="store_true", help="check the recorded hash")
    ap.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.verify:
        ok = verify_lock()
        print("split lock:", "VALID" if ok else "STALE -- corpus changed under it")
        return 0 if ok else 1

    doc = write_lock(force=args.force) if args.write else build_lock()
    c = doc["counts"]
    print(f"routines {c['routines']}  train {c['train']}  eval {c['eval']}")
    print(f"units {c['units']}  clusters held together {c['clusters_held_together']}"
          f" ({c['routines_in_clusters']} routines)")
    print(f"content_hash {doc['content_hash'][:16]}...")
    if args.write:
        print(f"written -> {DEFAULT_LOCK.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
