"""``rosetta.tools`` — the MCP tool server. The portable IP.

Eight tools over MUMPS/VistA source and the verifier, exposed as plain
JSON-RPC 2.0 over stdio with no third-party MCP SDK, so the demo runs offline
from a cold start. See :mod:`rosetta.tools.server` for the transport and a
ready-to-paste OpenCode configuration snippet.

Start it with::

    python -m rosetta.tools

Layout:

``server.py``    JSON-RPC 2.0 transport and dispatch
``tools.py``     the eight tools, their JSON schemas and model-facing descriptions
``sources.py``   routine source: benchmark corpus first, container fallback
``analysis.py``  static analysis, layered over ``rosetta.bench.select``
``fileman.py``   VistA data-dictionary lookup for ``resolve_global``
``report.py``    turns a ``VerifyReport`` into specific, actionable divergences
``runtime.py``   THE SEAM onto ``rosetta.core`` for the three execution tools
``cases.py``     input suites and benchmark task records
``ddcache.py``   build-time extractor for the FileMan dictionary cache
"""

from __future__ import annotations

__all__ = ["RosettaServer", "ToolRegistry", "main"]


def __getattr__(name: str):  # lazy so importing the package stays cheap
    if name in ("RosettaServer", "main"):
        from . import server

        return getattr(server, name)
    if name == "ToolRegistry":
        from .tools import ToolRegistry

        return ToolRegistry
    raise AttributeError(name)
