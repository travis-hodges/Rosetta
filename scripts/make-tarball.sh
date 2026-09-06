#!/usr/bin/env bash
# Build the release tarball and its SHA256SUMS.
#
#   scripts/make-tarball.sh OUTPUT_DIR
#
# Contents are chosen so that a clone is not required to use Rosetta: `rosetta demo`
# and `rosetta doctor` must work from an unpacked tarball with no network. That means
# data/ and results/canned/ ship, and it is why the manifest is a list of includes
# rather than excludes -- a new top-level directory should not silently publish.
#
# The archive is built with Python's tarfile, not tar(1): the flags that make a
# deterministic archive (--sort, --owner, --numeric-owner) are GNU-only, and macOS
# ships bsdtar. A checksum is worth nothing if it depends on which tar you ran.
set -euo pipefail

[[ $# == 1 ]] || { echo "Usage: scripts/make-tarball.sh OUTPUT_DIR" >&2; exit 2; }
out="$(cd -- "$1" && pwd)"
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

"${ROSETTA_PYTHON:-python3}" - "$root" "$out" <<'PY'
import gzip
import hashlib
import sys
import tarfile
from pathlib import Path

root, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
sys.path.insert(0, str(root))
import rosetta

name = f"rosetta-{rosetta.__version__}"
archive = out / f"{name}.tar.gz"

INCLUDE = [
    "rosetta", "bin", "scripts", "data", "results/canned", ".opencode",
    "docs", "README.md", "AGENTS.md", "CLAUDE.md",
    "LICENSE", "NOTICE", "pyproject.toml", "opencode.json",
]
missing = [p for p in INCLUDE if not (root / p).exists()]
if missing:
    sys.exit(f"error: refusing to build, missing: {', '.join(missing)}")

# Per-machine, generated, or simply none of a downloader's business.
EXCLUDE_NAMES = {"__pycache__", ".DS_Store", ".rosetta", "dist", "node_modules"}
EXCLUDE_PATHS = {"data/models.json"}


def members():
    for entry in INCLUDE:
        base = root / entry
        paths = [base] if base.is_file() else sorted(base.rglob("*"))
        for path in paths:
            rel = path.relative_to(root)
            if any(part in EXCLUDE_NAMES for part in rel.parts):
                continue
            if rel.as_posix() in EXCLUDE_PATHS or rel.suffix in {".pyc", ".pyo"}:
                continue
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                continue
            yield path, rel


def normalise(info: tarfile.TarInfo) -> tarfile.TarInfo:
    # Fixed identity and timestamp: rebuilding the same commit must produce the
    # same bytes, or the published checksum is not reproducible by anyone else.
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 1577836800  # 2020-01-01T00:00:00Z
    info.mode = 0o755 if (info.isdir() or info.mode & 0o100) else 0o644
    return info


collected = sorted(members(), key=lambda item: item[1].as_posix())
# gzip stamps the current time into its own header, which defeats reproducibility
# even when every tar member is normalised. Drive GzipFile directly with mtime=0.
with archive.open("wb") as raw:
    with gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=raw, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as tar:
            for path, rel in collected:
                info = tar.gettarinfo(str(path), arcname=f"{name}/{rel.as_posix()}")
                info = normalise(info)
                if info.isdir():
                    tar.addfile(info)
                else:
                    with path.open("rb") as stream:
                        tar.addfile(info, stream)

sha = hashlib.sha256(archive.read_bytes()).hexdigest()
(out / "SHA256SUMS").write_text(f"{sha}  {archive.name}\n", encoding="utf-8")
print(f"{sha}  {archive.name}")
print(f"{len(collected)} entries, {archive.stat().st_size / 1e6:.1f} MB")
PY
