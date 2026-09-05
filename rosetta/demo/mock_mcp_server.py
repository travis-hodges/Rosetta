#!/usr/bin/env python3
"""MOCK Rosetta MCP server -- stdio JSON-RPC. NOT the real tool server.

This exists for ONE reason: to prove that OpenCode can discover and call a
Rosetta stdio MCP server before Stream C's real server at `rosetta/tools/`
is ready. Every tool here is a stub. Every tool description is prefixed
"[MOCK]" so that a tool listing in OpenCode is unmistakably the mock.

When `rosetta/tools/` lands, point the OpenCode config at it instead --
see scripts/opencode-setup.sh, which takes the server module as an argument.
Nothing else in the wiring changes.

Protocol: MCP over stdio, newline-delimited JSON-RPC 2.0 on stdin/stdout.
Implemented methods: initialize, notifications/initialized, ping, tools/list,
tools/call, resources/list, prompts/list. Anything else returns -32601.

Stdlib only. Never writes to stdout except protocol frames -- diagnostics go
to stderr, because a stray print corrupts the stream and the failure is opaque.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable

SERVER_NAME = "rosetta-mock"
SERVER_VERSION = "0.0.1-mock"

# Set ROSETTA_MOCK_MCP_TRANSCRIPT=<path> to append every frame this server
# sees and sends, one JSON object per line. This is how we prove what a host
# like OpenCode actually asked for, rather than asserting it.
TRANSCRIPT_PATH = os.environ.get("ROSETTA_MOCK_MCP_TRANSCRIPT")


def _record(direction: str, frame: dict[str, Any]) -> None:
    if not TRANSCRIPT_PATH:
        return
    try:
        with open(TRANSCRIPT_PATH, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"dir": direction, "frame": frame}) + "\n")
    except OSError as exc:
        _log(f"transcript write failed: {exc}")

# Fallback if the client sends nothing we recognise. Real negotiation echoes
# the client's requested version back (see _initialize).
DEFAULT_PROTOCOL_VERSION = "2025-06-18"


def _log(msg: str) -> None:
    """Diagnostics to stderr only. stdout is the protocol channel."""
    print(f"[{SERVER_NAME}] {msg}", file=sys.stderr, flush=True)


# --- the mock tools -------------------------------------------------------
#
# Names mirror docs/PROJECT.md section 6 so that wiring proven here transfers
# verbatim to Stream C's server. Behaviour is fake and says so.


def _tool_echo(args: dict[str, Any]) -> str:
    text = args.get("text", "")
    if not isinstance(text, str):
        raise ValueError(f"echo: 'text' must be a string, got {type(text).__name__}")
    return f"MOCK ECHO: {text}"


def _tool_list_routines(args: dict[str, Any]) -> str:
    pattern = args.get("pattern") or "*"
    fake = ["PRCHUEI", "XLFSTR", "XLFCRC", "RGUTUU"]
    return json.dumps(
        {"mock": True, "pattern": pattern, "routines": fake},
        indent=2,
    )


def _tool_read_routine(args: dict[str, Any]) -> str:
    name = args.get("name")
    if not name:
        raise ValueError("read_routine: 'name' is required")
    return (
        f"; [MOCK] source for {name} is not available from the mock server.\n"
        f"; The real read_routine lands with rosetta/tools/ (Stream C)."
    )


def _tool_verify_change(args: dict[str, Any]) -> str:
    """The money tool, mocked. Returns a VerifyReport-shaped payload.

    Shape matches rosetta.core.interface.VerifyReport so that a client written
    against the mock parses the real server's output unchanged.
    """
    routine = args.get("routine")
    if not routine:
        raise ValueError("verify_change: 'routine' is required")
    if "candidate_src" not in args:
        raise ValueError("verify_change: 'candidate_src' is required")
    return json.dumps(
        {
            "mock": True,
            "equivalent": False,
            "n_cases": 5,
            "n_diverged": 1,
            "n_void": 0,
            "divergences": [
                {
                    "kind": "output",
                    "ref": "stdout",
                    "expected": "[MOCK] baseline value",
                    "actual": "[MOCK] candidate value",
                    "case_index": 0,
                }
            ],
        },
        indent=2,
    )


TOOLS: list[dict[str, Any]] = [
    {
        "name": "mock_echo",
        "description": "[MOCK] Echo text back. Proves the stdio transport is alive.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Text to echo"}},
            "required": ["text"],
        },
    },
    {
        "name": "list_routines",
        "description": "[MOCK] List VistA MUMPS routines. Returns a fixed fake list.",
        "inputSchema": {
            "type": "object",
            "properties": {"pattern": {"type": "string", "description": "Glob filter"}},
            "required": [],
        },
    },
    {
        "name": "read_routine",
        "description": "[MOCK] Read a routine's source. Returns a placeholder, not real source.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Routine name"}},
            "required": ["name"],
        },
    },
    {
        "name": "verify_change",
        "description": (
            "[MOCK] Differentially verify a candidate routine against its baseline. "
            "Always reports one fake divergence -- the real verifier is Stream A."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "routine": {"type": "string", "description": "Routine name"},
                "candidate_src": {"type": "string", "description": "Proposed source"},
            },
            "required": ["routine", "candidate_src"],
        },
    },
]

HANDLERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "mock_echo": _tool_echo,
    "list_routines": _tool_list_routines,
    "read_routine": _tool_read_routine,
    "verify_change": _tool_verify_change,
}


# --- JSON-RPC plumbing ----------------------------------------------------


def _initialize(params: dict[str, Any]) -> dict[str, Any]:
    requested = params.get("protocolVersion")
    version = requested if isinstance(requested, str) and requested else DEFAULT_PROTOCOL_VERSION
    client = (params.get("clientInfo") or {}).get("name", "<unknown>")
    _log(f"initialize from client={client!r} protocolVersion={version!r}")
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    }


def _tools_call(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    args = params.get("arguments") or {}
    handler = HANDLERS.get(name)
    if handler is None:
        # Tool-level errors are reported in-band via isError, not as JSON-RPC
        # errors -- that is what the MCP spec asks for, and it is what lets a
        # model see and react to the failure.
        return {
            "content": [{"type": "text", "text": f"Unknown tool: {name!r}"}],
            "isError": True,
        }
    try:
        text = handler(args)
    except Exception as exc:  # noqa: BLE001 -- surfaced to the caller in-band
        _log(f"tool {name!r} raised: {exc!r}")
        return {
            "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
            "isError": True,
        }
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _dispatch(method: str, params: dict[str, Any]) -> dict[str, Any]:
    if method == "initialize":
        return _initialize(params)
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        return _tools_call(params)
    # Declared capabilities do not include these, but some hosts probe anyway.
    # Empty lists are friendlier than an error and cost nothing.
    if method == "resources/list":
        return {"resources": []}
    if method == "prompts/list":
        return {"prompts": []}
    raise LookupError(method)


def serve(stdin: Any = None, stdout: Any = None) -> None:
    """Read newline-delimited JSON-RPC from stdin, write responses to stdout."""
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    _log("ready on stdio")
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            _log(f"unparseable frame, dropping: {exc}")
            continue

        _record("in", msg)
        method = msg.get("method")
        msg_id = msg.get("id")

        # A notification has no id and MUST NOT get a response.
        if msg_id is None:
            _log(f"notification {method!r}")
            continue

        try:
            result = _dispatch(method, msg.get("params") or {})
            response = {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except LookupError:
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        except Exception as exc:  # noqa: BLE001 -- must not kill the server
            _log(f"internal error on {method!r}: {exc!r}")
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": f"Internal error: {exc}"},
            }

        _record("out", response)
        stdout.write(json.dumps(response) + "\n")
        stdout.flush()
    _log("stdin closed, exiting")


if __name__ == "__main__":
    serve()
