"""Transparent stdio tap for an MCP server. Evidence, not plumbing.

Run an MCP server as a child process and relay stdin/stdout byte-for-byte
while appending every frame to a JSONL log. Point an MCP host (OpenCode) at
the tap instead of the server and the log is a verbatim record of what the
host actually asked for -- which is the only honest way to claim "OpenCode
discovered our tools".

    python3 -m rosetta.demo.mcp_tap --log /tmp/t.jsonl -- python3 -m rosetta.tools

Transparency rules this module obeys, because a tap that changes behaviour
proves nothing:
  * bytes are forwarded before they are logged, so the tap adds no round trip
  * a line that is not JSON is still forwarded, and logged as ``{"raw": ...}``
  * child stderr goes to our stderr untouched
  * a logging failure is reported on stderr and never breaks the stream
  * our exit status is the child's

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from typing import BinaryIO, Sequence

__all__ = ["run_tap", "main"]


def _append(path: str, record: dict[str, object], lock: threading.Lock) -> None:
    try:
        line = json.dumps(record, ensure_ascii=False)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        line = json.dumps({"ts": record.get("ts"), "log_error": str(exc)})
    try:
        with lock:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except OSError as exc:
        print(f"[mcp-tap] log write failed: {exc}", file=sys.stderr, flush=True)


def _pump(
    src: BinaryIO,
    dst: BinaryIO,
    direction: str,
    log_path: str | None,
    lock: threading.Lock,
) -> None:
    """Forward newline-delimited frames from src to dst, logging each one."""
    for raw in iter(src.readline, b""):
        try:
            dst.write(raw)
            dst.flush()
        except (BrokenPipeError, ValueError):
            break
        if not log_path:
            continue
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        record: dict[str, object] = {"ts": round(time.time(), 6), "dir": direction}
        try:
            record["frame"] = json.loads(text)
        except json.JSONDecodeError:
            record["raw"] = text
        _append(log_path, record, lock)
    try:
        dst.close()
    except (BrokenPipeError, ValueError, OSError):
        pass


def run_tap(command: Sequence[str], log_path: str | None = None) -> int:
    """Run ``command`` as an MCP server behind the tap. Returns its exit code."""
    if not command:
        raise ValueError("mcp_tap needs a server command to run")

    child = subprocess.Popen(
        list(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,  # inherit: server diagnostics stay visible
        env=os.environ.copy(),
    )
    assert child.stdin is not None and child.stdout is not None

    lock = threading.Lock()
    if log_path:
        _append(
            log_path,
            {"ts": round(time.time(), 6), "dir": "tap", "command": list(command)},
            lock,
        )

    up = threading.Thread(
        target=_pump,
        args=(sys.stdin.buffer, child.stdin, "host->server", log_path, lock),
        daemon=True,
    )
    down = threading.Thread(
        target=_pump,
        args=(child.stdout, sys.stdout.buffer, "server->host", log_path, lock),
        daemon=True,
    )
    up.start()
    down.start()

    code = child.wait()
    down.join(timeout=2.0)
    return code


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m rosetta.demo.mcp_tap",
        description="Relay an MCP stdio server and record every frame to JSONL.",
    )
    parser.add_argument("--log", default=os.environ.get("ROSETTA_MCP_TAP_LOG"))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(list(argv) if argv is not None else None)

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("give the server command after --")
    return run_tap(command, args.log)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
