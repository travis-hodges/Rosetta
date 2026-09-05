"""CLI for the mutation generator.

::

    # Survey the mutation surface without executing anything.
    python -m rosetta.mutate --survey --source-dir data/routines

    # Generate validated tasks (needs rosetta.core and a container).
    python -m rosetta.mutate --routines PRCHUEI,XLFSTR,XLFCRC \\
        --verifier core --out data/tasks/mutations.json

    # Exercise the pipeline with no container. Produces UNVALIDATED tasks.
    python -m rosetta.mutate --routines PRCHUEI --verifier static \\
        --no-require-split --out /tmp/preview.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from rosetta.mutate.cases import JsonGlobalSampler, build_cases
from rosetta.mutate.generate import generate_tasks, write_tasks
from rosetta.mutate.lex import parse_routine
from rosetta.mutate.operators import OPERATOR_NAMES, mutate_routine
from rosetta.mutate.verify import CoreNotAvailable, resolve_verifier


def _routine_name(path: Path) -> str:
    """``_DTC.m`` -> ``%DTC``; percent routines live on disk with an underscore."""
    stem = path.stem
    return ("%" + stem[1:] if stem.startswith("_") else stem).upper()


def _load_sources(args: argparse.Namespace) -> list[tuple[str, str]]:
    src_dir = Path(args.source_dir)
    if not src_dir.is_dir():
        raise SystemExit(f"source dir not found: {src_dir}")
    wanted = (
        {r.strip().upper() for r in args.routines.split(",") if r.strip()}
        if args.routines
        else None
    )
    out: list[tuple[str, str]] = []
    for p in sorted(src_dir.glob("*.m")):
        name = _routine_name(p)
        if wanted is not None and name not in wanted:
            continue
        out.append((name, p.read_text(errors="replace")))
    if wanted is not None:
        missing = wanted - {n for n, _ in out}
        if missing:
            raise SystemExit(f"routines not found in {src_dir}: {sorted(missing)}")
    return out


def _survey(sources: list[tuple[str, str]], ops: list[str] | None) -> dict:
    """Count candidate mutants per operator and difficulty. No execution."""
    per_op: dict[str, dict[str, int]] = {}
    per_routine: dict[str, int] = {}
    total = 0
    for name, src in sources:
        muts = mutate_routine(parse_routine(name, src), ops)
        per_routine[name] = len(muts)
        total += len(muts)
        for m in muts:
            row = per_op.setdefault(m.operator, {"easy": 0, "medium": 0, "hard": 0})
            row[m.difficulty] += 1
    top = sorted(per_routine.items(), key=lambda kv: -kv[1])[:20]
    return {
        "routines": len(sources),
        "candidate_mutants": total,
        "by_operator": per_op,
        "top_routines": dict(top),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point. Returns a process exit code."""
    ap = argparse.ArgumentParser(prog="rosetta.mutate", description=__doc__)
    ap.add_argument("--source-dir", default="data/routines")
    ap.add_argument("--routines", help="comma-separated routine names")
    ap.add_argument(
        "--operators",
        help=f"comma-separated subset of {','.join(OPERATOR_NAMES)}",
    )
    ap.add_argument("--survey", action="store_true", help="count candidates only")
    ap.add_argument("--cases", action="store_true", help="dump input suites only")
    ap.add_argument("--verifier", choices=("core", "static"), default="core")
    ap.add_argument("--split", default="data/tasks/split.lock.json")
    ap.add_argument("--no-require-split", action="store_true")
    ap.add_argument("--global-samples", help="JSON dump of real global values")
    ap.add_argument("--n-cases", type=int, default=10)
    ap.add_argument("--max-per-routine", type=int)
    ap.add_argument("--out", help="write tasks JSON here")
    args = ap.parse_args(argv)

    ops = (
        [o.strip().upper() for o in args.operators.split(",") if o.strip()]
        if args.operators
        else None
    )
    sources = _load_sources(args)

    if args.survey:
        json.dump(_survey(sources, ops), sys.stdout, indent=1)
        sys.stdout.write("\n")
        return 0

    if args.cases:
        from dataclasses import asdict

        out = {}
        for name, src in sources:
            r = parse_routine(name, src)
            out[name] = {
                label: [asdict(c) for c in build_cases(r, label, n_cases=args.n_cases)]
                for label in r.formals
                if r.formals[label]
            }
        json.dump(out, sys.stdout, indent=1)
        sys.stdout.write("\n")
        return 0

    verifier = resolve_verifier(args.verifier)
    if args.verifier == "static":
        print(
            "WARNING: --verifier static executes nothing. Every textual change "
            "is admitted, equivalent mutants are NOT filtered, and the output "
            "is not a benchmark. Do not publish numbers from it.",
            file=sys.stderr,
        )

    sampler = (
        JsonGlobalSampler.from_path(args.global_samples)
        if args.global_samples
        else None
    )
    try:
        result = generate_tasks(
            sources,
            verifier,
            split_path=args.split,
            require_split=not args.no_require_split,
            operators=ops,
            sampler=sampler,
            n_cases=args.n_cases,
            max_per_routine=args.max_per_routine,
        )
    except CoreNotAvailable as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(
        f"admitted {len(result.tasks)} tasks, "
        f"rejected {len(result.rejections)}: {result.reason_counts()}",
        file=sys.stderr,
    )
    if args.out:
        path = write_tasks(result, args.out)
        print(f"wrote {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
