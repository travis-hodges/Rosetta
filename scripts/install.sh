#!/usr/bin/env bash
# Install a source-checkout launcher without pip, network access, or shell edits.
set -euo pipefail

if [[ $# == 1 && ( "$1" == --help || "$1" == -h ) ]]; then
  echo 'Usage: bash scripts/install.sh --bin-dir PATH'
  echo 'Creates PATH/rosetta. Run `rosetta` from the project you want to open.'
  echo 'Keep this checkout and Python 3.11+ installed.'
  echo 'Set ROSETTA_PYTHON to select an interpreter. Existing files are never overwritten.'
  exit 0
fi
if [[ $# != 2 || "$1" != --bin-dir || -z "$2" ]]; then
  echo 'Usage: bash scripts/install.sh --bin-dir PATH' >&2
  exit 2
fi

ROSETTA_SOURCE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
"${ROSETTA_PYTHON:-python3}" - "$ROSETTA_SOURCE_ROOT" "$2" <<'PY'
import os
from pathlib import Path
import shlex
import sys

if sys.version_info < (3, 11):
    sys.exit("ERROR: Rosetta requires Python 3.11 or newer.")
root = Path(sys.argv[1]).resolve()
destination = Path(sys.argv[2]).expanduser().resolve() / "rosetta"
if not (root / "rosetta" / "cli.py").is_file():
    sys.exit("ERROR: Install from a complete Rosetta source checkout.")
program = (
    "import pathlib, sys; "
    f"root = pathlib.Path({str(root)!r}); "
    "(root / 'rosetta' / 'cli.py').is_file() or "
    "sys.exit('ERROR: Rosetta checkout moved or was removed; reinstall the launcher.'); "
    "sys.path.insert(0, str(root)); "
    "from rosetta.cli import main; sys.exit(main())"
)
launcher = "#!/bin/sh\nexec " + shlex.join([sys.executable, "-I", "-c", program]) + ' "$@"\n'
try:
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also refuses existing and broken symlinks.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(launcher)
except OSError as exc:
    sys.exit(f"ERROR: Could not install launcher: {exc}")
print(f"Installed {destination}")
print(f"Run {shlex.quote(str(destination))} from the project you want to open.")
print(f"Check setup with {shlex.quote(str(destination))} doctor")
print(f"To use 'rosetta' by name, add {destination.parent} to your PATH.")
PY
