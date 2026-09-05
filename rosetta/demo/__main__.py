"""``python3 -m rosetta.demo`` -- the canned side-by-side, and nothing else.

The default entry point is the safe one on purpose (docs/PROJECT.md #8: canned
path primary, interactive gated behind it). Everything riskier has to be asked
for by name:

    python3 -m rosetta.demo                      # canned, offline, no container
    python3 -m rosetta.demo --live               # re-record first, falls back
    python3 -m rosetta.demo.repair_loop --help   # the measuring harness
"""

from __future__ import annotations

import sys

from .sidebyside import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
