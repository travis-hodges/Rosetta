"""``python -m rosetta.tools`` — start the Rosetta MCP server on stdio."""

from __future__ import annotations

from .server import main

if __name__ == "__main__":
    raise SystemExit(main())
