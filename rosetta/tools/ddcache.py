"""BUILD-TIME extractor for the FileMan data-dictionary cache.

This module is **not** imported by the MCP server. It exists to produce
``data/fileman/dd.json.gz`` once, offline, from a running ``vehu`` container.
The server (``rosetta.tools.fileman``) only ever reads that file, which is what
keeps the demo runnable from a cold start with no network and no container.

Why a cache at all: PROJECT.md section 11 rule 3 reserves YottaDB access to
``rosetta/core/``. ``resolve_global`` needs FileMan schema and real sample
values, so we snapshot both here — read-only, at build time — rather than
letting a tool call reach into the database. The precedent is
``rosetta.bench.select.extract_from_container``, which pulls routine source the
same way.

What it reads (all read-only ``$ORDER`` / ``$GET``; nothing is written to the
container):

* ``^DIC(file,0)`` and ``^DIC(file,0,"GL")`` — file name and global root
* ``^DD(file,0,"NM",name)`` — file/subfile name
* ``^DD(file,0,"UP")`` — parent file for a subfile (a FileMan "multiple")
* ``^DD(file,field,0)`` — ``NAME^TYPE^CODES^NODE;PIECE^INPUT-TRANSFORM``
* a handful of real records under each populated global root, for samples

Sensitive pieces (ACCESS CODE, VERIFY CODE, electronic signature codes) are
redacted before anything is written to disk. The container's data is synthetic,
but credential-shaped fields do not belong in a committed artefact.

Usage::

    python -m rosetta.tools.ddcache --build
    python -m rosetta.tools.ddcache --build --out data/fileman/dd.json.gz
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

__all__ = [
    "CACHE_SCHEMA",
    "DEFAULT_CACHE_PATH",
    "ContainerSpec",
    "build_cache",
    "main",
]

CACHE_SCHEMA = "rosetta.tools/fileman-dd/1"
DEFAULT_CACHE_PATH = Path("data/fileman/dd.json.gz")

#: Field names whose stored value is a credential or signature code. Matched
#: case-insensitively against the FileMan field name; the corresponding piece
#: of every sampled node is replaced before the cache is written.
_SENSITIVE_FIELD_RE = re.compile(
    r"(ACCESS CODE|VERIFY CODE|ELECTRONIC SIGNATURE|SIGNATURE CODE|"
    r"PASSWORD|CRYPTOGRAPHIC|SECRET)",
    re.IGNORECASE,
)
REDACTED = "<redacted>"

#: Dump the whole dictionary: every file that either has a global root
#: (``^DIC(f,0,"GL")``) or is a subfile (``^DD(f,0,"UP")``).
_DD_DUMP = (
    'S U="^",F=0 F  S F=$O(^DD(F)) Q:F\'>0  I $D(^DIC(F,0,"GL"))!$D(^DD(F,0,"UP")) '
    'S NM=$O(^DD(F,0,"NM","")) S:NM="" NM=$P($G(^DIC(F,0)),U) '
    'W "H|",F,"|",$TR(NM,"|","/"),"|",$G(^DIC(F,0,"GL")),"|",$G(^DD(F,0,"UP")),"|",'
    '$P($G(^DD(F,0)),U,4),! '
    "S X=0 F  S X=$O(^DD(F,X)) Q:X'>0  S Z=$G(^DD(F,X,0)) "
    'W "D|",F,"|",X,"|",$TR($P(Z,U),"|","/"),"|",$TR($P(Z,U,2),"|","/"),"|",'
    '$TR($P(Z,U,3),"|","/"),"|",$TR($P(Z,U,4),"|","/"),!\n'
    'W "ENDDD",!\n'
    "H\n"
)

#: Sample real records. ``n_ien`` top-level entries per file, ``n_node`` nodes
#: each. Both root shapes occur: ``^DPT(`` for a file that owns its global and
#: ``^VA(200,`` for one nested inside a shared global.
_SAMPLE_DUMP = (
    'S U="^",F=0 F  S F=$O(^DIC(F)) Q:F\'>0  S R=$G(^DIC(F,0,"GL")) '
    'I R\'="","(,"[$E(R,$L(R)) S I=0,N=0 F  S I=$O(@(R_"I)")) Q:I=""  Q:+I\'=I  '
    "S N=N+1 Q:N>{n_ien}  S J=\"\",M=0 F  S J=$O(@(R_\"I,J)\")) Q:J=\"\"  "
    'S M=M+1 Q:M>{n_node}  W "S|",R,"|",R_I_","_J_")|",$E($G(@(R_"I,J)")),1,{maxlen}),!\n'
    'W "ENDS",!\n'
    "H\n"
)


@dataclass(frozen=True)
class ContainerSpec:
    """Where the vehu container keeps its M runtime. See PROJECT.md section 14."""

    name: str = "vehu"
    gtm_dist: str = "/home/vehu/lib/gtm"
    gtmgbldir: str = "/home/vehu/g/vehu.gld"

    @property
    def mumps(self) -> str:
        return f"{self.gtm_dist}/mumps"


def _run_m(spec: ContainerSpec, script: str, timeout_s: float = 300.0) -> str:
    """Pipe an M script into ``mumps -direct`` in the container; return stdout.

    Direct mode is used deliberately: it needs no routine written into the
    container's ``/home/vehu/r``, so the extraction leaves no trace.
    """
    cmd = [
        "docker", "exec", "-i",
        "-e", f"gtm_dist={spec.gtm_dist}",
        "-e", f"gtmgbldir={spec.gtmgbldir}",
        spec.name, spec.mumps, "-direct",
    ]
    proc = subprocess.run(
        cmd, input=script, capture_output=True, text=True, timeout=timeout_s
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"mumps -direct failed (rc={proc.returncode}) in container "
            f"{spec.name!r}: {proc.stderr.strip()[:500]}"
        )
    if proc.stderr.strip():
        print(f"[ddcache] M stderr: {proc.stderr.strip()[:500]}", file=sys.stderr)
    return proc.stdout


def _parse_dd(out: str) -> dict[str, dict[str, Any]]:
    if "ENDDD" not in out:
        raise RuntimeError("DD dump did not reach its end marker; output truncated")
    files: dict[str, dict[str, Any]] = {}
    for line in out.splitlines():
        if line.startswith("H|"):
            _, num, name, root, up, count = (line.split("|") + [""] * 6)[:6]
            files[num] = {
                "name": name.strip(),
                "root": root.strip(),
                "up": up.strip(),
                "field_count": count.strip(),
                "fields": {},
            }
        elif line.startswith("D|"):
            parts = line.split("|")
            if len(parts) < 7:
                continue
            _, num, fld, fname, ftype, codes, storage = parts[:7]
            rec = files.get(num)
            if rec is None:
                continue
            rec["fields"][fld] = [fname.strip(), ftype.strip(), codes.strip(), storage.strip()]
    return files


def _parse_samples(out: str) -> dict[str, list[list[str]]]:
    if "ENDS" not in out:
        raise RuntimeError("sample dump did not reach its end marker; output truncated")
    samples: dict[str, list[list[str]]] = {}
    for line in out.splitlines():
        if not line.startswith("S|"):
            continue
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        _, root, ref, value = parts
        samples.setdefault(root, []).append([ref, value])
    return samples


def _sensitive_pieces(files: dict[str, dict[str, Any]]) -> dict[str, dict[str, set[int]]]:
    """Map file number -> node -> {piece numbers holding credential material}."""
    out: dict[str, dict[str, set[int]]] = {}
    for num, rec in files.items():
        for fname, ftype, _codes, storage in rec["fields"].values():
            if not _SENSITIVE_FIELD_RE.search(fname):
                continue
            if ";" not in storage:
                continue
            node, _, piece = storage.partition(";")
            if not piece.isdigit():
                continue
            out.setdefault(num, {}).setdefault(node, set()).add(int(piece))
    return out


def _redact(
    samples: dict[str, list[list[str]]],
    files: dict[str, dict[str, Any]],
) -> int:
    """Blank credential-shaped pieces in place. Returns the count redacted."""
    root_to_file = {r["root"]: n for n, r in files.items() if r["root"]}
    sensitive = _sensitive_pieces(files)
    n = 0
    for root, rows in samples.items():
        fnum = root_to_file.get(root)
        if fnum is None or fnum not in sensitive:
            continue
        by_node = sensitive[fnum]
        for row in rows:
            ref, value = row
            node = ref.rstrip(")").rsplit(",", 1)[-1]
            pieces_to_hide = by_node.get(node)
            if not pieces_to_hide:
                continue
            pieces = value.split("^")
            for p in pieces_to_hide:
                if 1 <= p <= len(pieces) and pieces[p - 1]:
                    pieces[p - 1] = REDACTED
                    n += 1
            row[1] = "^".join(pieces)
    return n


def build_cache(
    out_path: Path,
    spec: ContainerSpec | None = None,
    n_ien: int = 4,
    n_node: int = 8,
    maxlen: int = 400,
) -> dict[str, Any]:
    """Extract the dictionary and samples, redact, and write a gzipped cache.

    Returns the cache dict that was written.
    """
    spec = spec or ContainerSpec()
    files = _parse_dd(_run_m(spec, _DD_DUMP))
    samples = _parse_samples(
        _run_m(spec, _SAMPLE_DUMP.format(n_ien=n_ien, n_node=n_node, maxlen=maxlen))
    )
    n_redacted = _redact(samples, files)

    cache: dict[str, Any] = {
        "schema": CACHE_SCHEMA,
        "generated_by": "rosetta/tools/ddcache.py",
        "source": {
            "container": spec.name,
            "gtm_dist": spec.gtm_dist,
            "gtmgbldir": spec.gtmgbldir,
            "access": "read-only $ORDER/$GET via mumps -direct; nothing written",
        },
        "field_record": ["name", "type", "codes", "storage"],
        "counts": {
            "files": len(files),
            "fields": sum(len(r["fields"]) for r in files.values()),
            "sampled_roots": len(samples),
            "sample_nodes": sum(len(v) for v in samples.values()),
            "redacted_pieces": n_redacted,
        },
        "files": files,
        "samples": samples,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", encoding="utf-8", compresslevel=9) as fh:
        json.dump(cache, fh, separators=(",", ":"), sort_keys=True)
    return cache


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point. Returns a process exit code."""
    root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__ or "")
    ap.add_argument("--build", action="store_true", help="extract and write the cache")
    ap.add_argument("--out", type=Path, default=root / DEFAULT_CACHE_PATH)
    ap.add_argument("--container", default="vehu")
    ap.add_argument("--n-ien", type=int, default=4)
    ap.add_argument("--n-node", type=int, default=8)
    args = ap.parse_args(argv)
    if not args.build:
        ap.error("nothing to do; pass --build")
    cache = build_cache(
        args.out,
        ContainerSpec(name=args.container),
        n_ien=args.n_ien,
        n_node=args.n_node,
    )
    print(json.dumps({"out": str(args.out), **cache["counts"]}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
