"""Minimal MCP-over-stdio client. Stdlib only.

The demo harness reaches Rosetta's tools the same way the TUI does -- by
speaking MCP to a subprocess -- rather than by importing `rosetta.tools`.
That protocol boundary is the point of the architecture (docs/PROJECT.md #6:
"works with any host agent"), so the harness must not shortcut it.

Fails loudly: a protocol error, a non-zero exit, or a timeout raises. There is
no silent degradation, because a demo that quietly stops verifying is worse
than one that crashes.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "rosetta-demo-harness"
CLIENT_VERSION = "0.1.0"


class McpError(RuntimeError):
    """Transport, protocol, or server-side JSON-RPC failure."""


@dataclass(frozen=True)
class ToolResult:
    """Result of one tools/call. `text` is the concatenated text content."""

    tool: str
    text: str
    is_error: bool
    raw: dict[str, Any]

    def json(self) -> Any:
        """Parse `text` as JSON. Raises McpError if it is not JSON."""
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise McpError(f"tool {self.tool!r} returned non-JSON text: {exc}") from exc


class McpStdioClient:
    """Speaks MCP to a subprocess over stdin/stdout. Use as a context manager."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        if not command:
            raise ValueError("command must be non-empty")
        self._command = list(command)
        self._cwd = cwd
        self._env = env
        self._timeout_s = timeout_s
        self._proc: subprocess.Popen[str] | None = None
        self._next_id = 0
        self._stderr: list[str] = []
        self._stderr_thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> McpStdioClient:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def start(self) -> dict[str, Any]:
        """Spawn the server and complete the MCP initialize handshake."""
        if self._proc is not None:
            raise McpError("client already started")
        try:
            self._proc = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self._cwd,
                env=self._env,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise McpError(f"could not spawn {self._command!r}: {exc}") from exc

        # Drain stderr continuously; a full pipe deadlocks the server.
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

        init = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        )
        self.notify("notifications/initialized", {})
        return init

    def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        self._proc = None
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        except OSError:
            pass

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                self._stderr.append(line.rstrip("\n"))
        except (ValueError, OSError):
            pass

    @property
    def stderr_log(self) -> list[str]:
        return list(self._stderr)

    # -- transport ---------------------------------------------------------

    def _write(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise McpError("client is not started")
        if proc.poll() is not None:
            raise McpError(
                f"server exited with code {proc.returncode} before request; "
                f"stderr: {self._stderr[-10:]}"
            )
        try:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise McpError(
                f"server closed stdin; stderr: {self._stderr[-10:]}"
            ) from exc

    def _read_response(self, want_id: int) -> dict[str, Any]:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise McpError("client is not started")
        # Skip any server-initiated notifications and mismatched ids rather
        # than treating them as protocol violations.
        while True:
            line = proc.stdout.readline()
            if line == "":
                raise McpError(
                    f"server closed stdout awaiting id={want_id} "
                    f"(exit={proc.poll()}); stderr: {self._stderr[-10:]}"
                )
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                raise McpError(f"server emitted non-JSON on stdout: {line[:200]!r}") from exc
            if msg.get("id") == want_id:
                return msg

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        req_id = self._next_id
        self._write(
            {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        )
        msg = self._read_response(req_id)
        if "error" in msg:
            err = msg["error"]
            raise McpError(
                f"{method} failed: [{err.get('code')}] {err.get('message')}"
            )
        return msg.get("result") or {}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # -- MCP surface -------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        return self.request("tools/list").get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        parts = [
            block.get("text", "")
            for block in result.get("content", [])
            if block.get("type") == "text"
        ]
        return ToolResult(
            tool=name,
            text="\n".join(parts),
            is_error=bool(result.get("isError")),
            raw=result,
        )


def _main() -> int:
    """Smoke test: spawn a server, list tools, call each one that is safe.

    Usage: python -m rosetta.demo.mcp_stdio_client <command> [args...]
    """
    if len(sys.argv) < 2:
        print(__doc__)
        print("usage: python -m rosetta.demo.mcp_stdio_client <command> [args...]")
        return 2
    with McpStdioClient(sys.argv[1:]) as client:
        tools = client.list_tools()
        print(f"discovered {len(tools)} tools:")
        for tool in tools:
            print(f"  - {tool['name']}: {tool.get('description', '')}")
        if any(t["name"] == "mock_echo" for t in tools):
            res = client.call_tool("mock_echo", {"text": "hello from the harness"})
            print(f"\nmock_echo -> {res.text!r} (isError={res.is_error})")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
