"""Rosetta MCP server — plain JSON-RPC 2.0 over stdio, stdlib only.

The portable IP. It works with any host agent that speaks MCP, which is the
answer to "what if the labs eat the agent layer?" — and it is how the
tools-on arm of the benchmark actually reaches the model.

No third-party MCP SDK is used on purpose. PROJECT.md section 11 requires the
demo to run offline from a cold start; a stdlib-only transport keeps that
guarantee and removes an install step from the riskiest hour of the build.

Transport
---------
Newline-delimited JSON on stdin/stdout, one JSON-RPC message per line — the MCP
stdio framing. **Nothing but JSON-RPC ever goes to stdout**; all logging goes to
stderr. Implemented methods: ``initialize``, ``notifications/initialized``,
``ping``, ``tools/list``, ``tools/call``. Batch arrays are accepted.

Failure policy: a *protocol* problem (bad JSON, unknown method, malformed
params) returns a JSON-RPC error object. A *tool* problem (routine not found,
verifier not available) returns a normal result with ``isError: true`` and a
readable explanation, because the agent is supposed to read it and try
something else. Neither ever crashes the server or hangs the pipe.

Harness configuration
---------------------
Add to ``opencode.json`` in the project root (or ``~/.config/opencode/config.json``)::

    {
      "$schema": "https://opencode.ai/config.json",
      "mcp": {
        "rosetta": {
          "type": "local",
          "command": ["python3", "-m", "rosetta.tools"],
          "enabled": true,
          "environment": {
            "PYTHONPATH": "/absolute/path/to/Rosetta",
            "ROSETTA_TOOLS_BACKEND": "auto",
            "ROSETTA_TOOLS_CONTAINER": "vehu"
          }
        }
      }
    }

For a host using the ``mcpServers`` shape (Claude Desktop and friends)::

    {
      "mcpServers": {
        "rosetta": {
          "command": "python3",
          "args": ["-m", "rosetta.tools"],
          "env": {"PYTHONPATH": "/absolute/path/to/Rosetta"}
        }
      }
    }

Environment
-----------
``ROSETTA_TOOLS_BACKEND``   ``auto`` (default) | ``core`` | ``none``
``ROSETTA_TOOLS_CONTAINER`` container name for routine-source fallback, or ``off``
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import dataclass
from typing import Any, BinaryIO, Callable, Iterable, Sequence, TextIO

from .runtime import backend_status
from .tools import Tool, ToolError, ToolRegistry

__all__ = [
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "JsonRpcError",
    "RosettaServer",
    "main",
]

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "rosetta"
SERVER_VERSION = "0.1.0"

#: JSON-RPC 2.0 reserved codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

#: Cap on a single text content block, so one huge routine cannot blow the
#: host's context or the pipe.
MAX_TEXT_CHARS = 120_000


class JsonRpcError(Exception):
    """A protocol-level error; becomes a JSON-RPC ``error`` object."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_obj(self) -> dict[str, Any]:
        obj: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            obj["data"] = self.data
        return obj


@dataclass
class _Request:
    method: str
    params: dict[str, Any]
    id: Any
    is_notification: bool


def _truncate(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more characters]"


def _tool_descriptor(tool: Tool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "title": tool.title,
        "description": tool.description,
        "inputSchema": tool.input_schema,
        "annotations": {
            "readOnlyHint": not tool.requires_execution,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }


class RosettaServer:
    """JSON-RPC dispatcher. Transport-agnostic; :meth:`serve` adds stdio."""

    def __init__(self, registry: ToolRegistry | None = None, log: TextIO | None = None) -> None:
        self.registry = registry or ToolRegistry()
        self.log = log if log is not None else sys.stderr
        self.initialized = False
        self._negotiated_version = PROTOCOL_VERSION
        self._methods: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "initialize": self._initialize,
            "ping": lambda params: {},
            "tools/list": self._tools_list,
            "tools/call": self._tools_call,
        }
        self._notifications = {
            "notifications/initialized",
            "notifications/cancelled",
            "initialized",
        }

    # -- logging -----------------------------------------------------------

    def _warn(self, message: str) -> None:
        try:
            print(f"[rosetta-mcp] {message}", file=self.log, flush=True)
        except (OSError, ValueError):  # log pipe gone; never fatal
            pass

    # -- methods -----------------------------------------------------------

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
            self._negotiated_version = requested
        self.initialized = True
        status = backend_status()
        return {
            "protocolVersion": self._negotiated_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": SERVER_NAME,
                "title": "Rosetta — legacy-system editor tools",
                "version": SERVER_VERSION,
            },
            "instructions": self._instructions(status),
            "_rosetta": {
                "backend": status,
                "corpus_routines": len(self.registry.store.corpus_names()),
                "fileman_dictionary": self.registry.dictionary.counts,
                "tools_live_without_runtime": [
                    t.name for t in self.registry.list() if not t.requires_execution
                ],
                "tools_needing_runtime": [
                    t.name for t in self.registry.list() if t.requires_execution
                ],
            },
        }

    def _instructions(self, status: dict[str, Any]) -> str:
        base = (
            "These tools read and verify MUMPS (M) code from VistA, the US "
            "Department of Veterans Affairs health system. MUMPS has no tables: "
            "the database is a set of sparse persistent arrays written with a "
            "leading caret, such as ^DPT(3,0), whose value is one string with "
            "fields packed into '^'-delimited pieces. Use resolve_global to "
            "learn what any of those pieces mean before changing code that "
            "touches them.\n\n"
            "Begin with the operator's requested outcome. Use list_routines, "
            "read_routine, parse_routine, call_graph, and resolve_global to "
            "discover the affected code and data before editing. FileMan "
            "content is created or updated with `rosetta fileman`; other "
            "persistent state uses a captured Rosetta database plan. Use "
            "verify_change at the final gate for behavior that must remain "
            "equivalent; intentional feature behavior requires its own change "
            "contract and must not be erased to satisfy equivalence."
        )
        if not status.get("execution_available"):
            base += (
                "\n\nNOTE: the execution backend is not available in this "
                "session, so execute_routine, verify_change and run_task_cases "
                f"will return an error ({status.get('reason', '')}). The five "
                "static tools work normally."
            )
        return base

    def _tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"tools": [_tool_descriptor(t) for t in self.registry.list()]}

    def _tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise JsonRpcError(INVALID_PARAMS, "tools/call requires a 'name' string")
        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise JsonRpcError(
                INVALID_PARAMS, "tools/call 'arguments' must be an object"
            )
        try:
            tool = self.registry.get(name)
        except KeyError:
            raise JsonRpcError(
                INVALID_PARAMS,
                f"unknown tool: {name}",
                {"available": [t.name for t in self.registry.list()]},
            ) from None
        try:
            result = tool.handler(arguments)
        except ToolError as exc:
            return self._error_result(name, str(exc), exc.data)
        except (ValueError, LookupError, OSError) as exc:
            return self._error_result(
                name, f"{type(exc).__name__}: {exc}", {"recoverable": True}
            )
        except Exception as exc:  # never let a tool kill the server
            self._warn(f"unhandled error in {name}: {traceback.format_exc()}")
            return self._error_result(
                name,
                f"internal error in tool {name}: {type(exc).__name__}: {exc}",
                {"recoverable": False},
            )
        return self._ok_result(result)

    # -- result shaping ----------------------------------------------------

    def _ok_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = self._render_text(payload)
        return {
            "content": [{"type": "text", "text": _truncate(text)}],
            "structuredContent": payload,
            "isError": False,
        }

    @staticmethod
    def _render_text(payload: dict[str, Any]) -> str:
        """Human-first text block. The model reads this before the JSON.

        Source is emitted verbatim rather than JSON-escaped — a model reading
        ``\\n``-escaped MUMPS makes avoidable mistakes. A ``feedback`` or
        ``explanation`` field leads, and is then dropped from the JSON body so
        it is not duplicated.
        """
        if "source" in payload and isinstance(payload["source"], str):
            meta = {k: v for k, v in payload.items() if k != "source"}
            header = json.dumps(meta, indent=2, default=str)
            return f"{header}\n\n----- source -----\n{payload['source']}"
        for key in ("feedback", "explanation"):
            lead = payload.get(key)
            if isinstance(lead, str) and lead:
                rest = {k: v for k, v in payload.items() if k != key}
                return f"{lead}\n\n{json.dumps(rest, indent=2, default=str)}"
        return json.dumps(payload, indent=2, default=str)

    def _error_result(
        self, tool: str, message: str, data: dict[str, Any] | None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"error": message, "tool": tool}
        if data:
            payload.update(data)
        text = message
        if data:
            text += "\n\n" + json.dumps(data, indent=2, default=str)
        return {
            "content": [{"type": "text", "text": _truncate(text)}],
            "structuredContent": payload,
            "isError": True,
        }

    # -- dispatch ----------------------------------------------------------

    def _parse_request(self, raw: Any) -> _Request:
        if not isinstance(raw, dict):
            raise JsonRpcError(INVALID_REQUEST, "request must be a JSON object")
        if raw.get("jsonrpc") != "2.0":
            raise JsonRpcError(INVALID_REQUEST, "jsonrpc must be exactly \"2.0\"")
        method = raw.get("method")
        if not isinstance(method, str) or not method:
            raise JsonRpcError(INVALID_REQUEST, "missing or non-string 'method'")
        params = raw.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, (dict, list)):
            raise JsonRpcError(INVALID_PARAMS, "'params' must be an object or array")
        if isinstance(params, list):
            raise JsonRpcError(
                INVALID_PARAMS, "positional params are not supported; send an object"
            )
        has_id = "id" in raw
        rid = raw.get("id")
        if has_id and not isinstance(rid, (str, int, float, type(None))):
            raise JsonRpcError(INVALID_REQUEST, "'id' must be a string, number or null")
        return _Request(method=method, params=params, id=rid, is_notification=not has_id)

    def handle_message(self, raw: Any) -> dict[str, Any] | None:
        """Dispatch one parsed message. ``None`` means "send nothing" (notification)."""
        try:
            req = self._parse_request(raw)
        except JsonRpcError as exc:
            rid = raw.get("id") if isinstance(raw, dict) else None
            return {"jsonrpc": "2.0", "id": rid, "error": exc.to_obj()}

        if req.is_notification:
            if req.method not in self._notifications:
                self._warn(f"ignoring unknown notification: {req.method}")
            return None

        handler = self._methods.get(req.method)
        if handler is None:
            code = METHOD_NOT_FOUND
            msg = f"method not found: {req.method}"
            data: Any = {"supported": sorted(self._methods)}
            return {
                "jsonrpc": "2.0",
                "id": req.id,
                "error": JsonRpcError(code, msg, data).to_obj(),
            }
        try:
            result = handler(req.params)
        except JsonRpcError as exc:
            return {"jsonrpc": "2.0", "id": req.id, "error": exc.to_obj()}
        except Exception as exc:  # pragma: no cover - defensive
            self._warn(f"unhandled error in {req.method}: {traceback.format_exc()}")
            return {
                "jsonrpc": "2.0",
                "id": req.id,
                "error": JsonRpcError(
                    INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"
                ).to_obj(),
            }
        return {"jsonrpc": "2.0", "id": req.id, "result": result}

    def handle_line(self, line: str) -> str | None:
        """Parse one line and return the response line, or ``None``."""
        line = line.strip()
        if not line:
            return None
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            err = JsonRpcError(PARSE_ERROR, f"invalid JSON: {exc}")
            return json.dumps({"jsonrpc": "2.0", "id": None, "error": err.to_obj()})
        if isinstance(raw, list):
            if not raw:
                err = JsonRpcError(INVALID_REQUEST, "empty batch")
                return json.dumps({"jsonrpc": "2.0", "id": None, "error": err.to_obj()})
            responses = [r for r in (self.handle_message(m) for m in raw) if r is not None]
            return json.dumps(responses, default=str) if responses else None
        response = self.handle_message(raw)
        return json.dumps(response, default=str) if response is not None else None

    # -- stdio loop --------------------------------------------------------

    def serve(self, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
        """Read newline-delimited JSON-RPC from stdin until EOF."""
        src = stdin if stdin is not None else sys.stdin
        dst = stdout if stdout is not None else sys.stdout
        self._warn(
            f"ready: {len(self.registry.list())} tools, backend="
            f"{backend_status()['backend']}"
        )
        while True:
            try:
                line = src.readline()
            except KeyboardInterrupt:
                return 0
            except (OSError, ValueError):
                return 0
            if line == "":
                return 0
            try:
                out = self.handle_line(line)
            except Exception:  # pragma: no cover - last-resort guard
                self._warn(f"dispatcher fault: {traceback.format_exc()}")
                out = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": INTERNAL_ERROR, "message": "dispatcher fault"},
                    }
                )
            if out is None:
                continue
            try:
                dst.write(out + "\n")
                dst.flush()
            except BrokenPipeError:
                return 0
            except (OSError, ValueError):
                return 1


def run_script(server: RosettaServer, messages: Iterable[dict[str, Any]]) -> list[Any]:
    """Drive the dispatcher directly. Used by ``--selftest`` and the tests."""
    out: list[Any] = []
    for msg in messages:
        line = server.handle_line(json.dumps(msg))
        if line is not None:
            out.append(json.loads(line))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point. Returns a process exit code."""
    ap = argparse.ArgumentParser(
        prog="python -m rosetta.tools", description=__doc__ or ""
    )
    ap.add_argument(
        "--selftest",
        action="store_true",
        help="run an initialize/tools/list/tools/call exchange and print it",
    )
    ap.add_argument(
        "--list-tools", action="store_true", help="print tool names and exit"
    )
    args = ap.parse_args(argv)

    server = RosettaServer()
    if args.list_tools:
        for t in server.registry.list():
            flag = "exec" if t.requires_execution else "static"
            print(f"{t.name:18s} [{flag}] {t.title}")
        return 0
    if args.selftest:
        script = [
            {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "selftest", "version": "0"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "resolve_global", "arguments": {"ref": "^DPT(3,0)"}},
            },
        ]
        for response in run_script(server, script):
            print(json.dumps(response, indent=2)[:4000])
        return 0
    return server.serve()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
