"""``python3 -m rosetta.gui`` -- same as ``rosetta gui``."""

from __future__ import annotations

from rosetta.gui.server import main

if __name__ == "__main__":
    raise SystemExit(main())
