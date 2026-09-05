#!/usr/bin/env bash
# Register Rosetta's MCP tool server with OpenCode. Idempotent.
#
# Verified against OpenCode 1.18.29 (npm package `opencode-ai`), MCP protocol
# 2025-11-25. That version reads `opencode.json` from the project root and takes
# a local stdio server as:
#
#   "mcp": { "<name>": { "type": "local", "command": [...],
#                        "enabled": true, "environment": {...} } }
#
# `command` is a single argv array (executable first) -- not the
# `{"command": "...", "args": [...]}` shape Claude Desktop uses. OpenCode also
# accepts the `mcpServers` key for compatibility; this script writes the native
# shape because that is the one this version documents in `opencode mcp add`.
#
# Usage:
#   scripts/opencode-setup.sh                    # register rosetta -> rosetta.tools
#   scripts/opencode-setup.sh --module rosetta.demo.mock_mcp_server --name rosetta-mock
#   scripts/opencode-setup.sh --trace /tmp/rosetta-mcp.jsonl   # record every frame
#   scripts/opencode-setup.sh --check            # verify only, change nothing
#   scripts/opencode-setup.sh --print            # print the snippet, write nothing
#
# No sudo. No network. Only writes ./opencode.json in the repository root.

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${REPO_ROOT}/opencode.json"

SERVER_NAME="rosetta"
SERVER_MODULE="rosetta.tools"
CONTAINER="${ROSETTA_TOOLS_CONTAINER:-vehu}"
BACKEND="${ROSETTA_TOOLS_BACKEND:-auto}"
TRACE_LOG=""
MODE="write"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)      SERVER_NAME="$2"; shift 2 ;;
    --module)    SERVER_MODULE="$2"; shift 2 ;;
    --container) CONTAINER="$2"; shift 2 ;;
    --backend)   BACKEND="$2"; shift 2 ;;
    --trace)     TRACE_LOG="$2"; shift 2 ;;
    --check)     MODE="check"; shift ;;
    --print)     MODE="print"; shift ;;
    -h|--help)   sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

PY="$(command -v python3 || true)"
if [[ -z "${PY}" ]]; then
  echo "python3 not found on PATH" >&2
  exit 1
fi

# ---------------------------------------------------------------- the snippet
SNIPPET="$(
  REPO_ROOT="${REPO_ROOT}" SERVER_NAME="${SERVER_NAME}" SERVER_MODULE="${SERVER_MODULE}" \
  CONTAINER="${CONTAINER}" BACKEND="${BACKEND}" TRACE_LOG="${TRACE_LOG}" \
  "${PY}" - <<'PYEOF'
import json, os

root = os.environ["REPO_ROOT"]
module = os.environ["SERVER_MODULE"]
trace = os.environ["TRACE_LOG"]

command = ["python3", "-m", module]
env = {
    "PYTHONPATH": root,
    "PYTHONUNBUFFERED": "1",
    "ROSETTA_TOOLS_BACKEND": os.environ["BACKEND"],
    "ROSETTA_TOOLS_CONTAINER": os.environ["CONTAINER"],
}
if trace:
    command = ["python3", "-m", "rosetta.demo.mcp_tap", "--"] + command
    env["ROSETTA_MCP_TAP_LOG"] = trace

print(json.dumps({
    "$schema": "https://opencode.ai/config.json",
    "mcp": {
        os.environ["SERVER_NAME"]: {
            "type": "local",
            "command": command,
            "enabled": True,
            "environment": env,
        }
    },
}, indent=2))
PYEOF
)"

if [[ "${MODE}" == "print" ]]; then
  printf '%s\n' "${SNIPPET}"
  exit 0
fi

# ------------------------------------------------------------------- write it
#
# Merge, never overwrite. opencode.json is shared: it also carries the Rosetta
# agent definitions, permission rules and instruction paths that another
# workstream owns. Writing the whole file from here would silently delete them.
if [[ "${MODE}" == "write" ]]; then
  CONFIG="${CONFIG}" SERVER_NAME="${SERVER_NAME}" SERVER_MODULE="${SERVER_MODULE}" \
  SNIPPET="${SNIPPET}" "${PY}" - <<'PYEOF'
import json, os, sys

path = os.environ["CONFIG"]
name = os.environ["SERVER_NAME"]
entry = json.loads(os.environ["SNIPPET"])["mcp"][name]

existing = {}
if os.path.exists(path):
    with open(path, encoding="utf-8") as handle:
        text = handle.read().strip()
    if text:
        try:
            existing = json.loads(text)
        except json.JSONDecodeError as exc:
            # Refuse to guess. Clobbering a hand-edited config is worse than
            # stopping and saying so.
            sys.exit(f"{path} is not valid JSON ({exc}); fix it or move it aside")

existing.setdefault("$schema", "https://opencode.ai/config.json")
servers = existing.setdefault("mcp", {})
if servers.get(name) == entry:
    print(f"opencode.json already registers {name!r} -> {os.environ['SERVER_MODULE']} (unchanged)")
    raise SystemExit(0)

servers[name] = entry
with open(path, "w", encoding="utf-8") as handle:
    handle.write(json.dumps(existing, indent=2) + "\n")
other = sorted(k for k in existing if k not in ("$schema", "mcp"))
print(f"updated {path}  ({name!r} -> python3 -m {os.environ['SERVER_MODULE']})")
if other:
    print("  preserved existing keys: " + ", ".join(other))
PYEOF
fi

# ------------------------------------------------------------------- verify it
echo
echo "--- the server answers tools/list on its own ---"
(
  cd "${REPO_ROOT}"
  printf '%s\n%s\n' \
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
    '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | PYTHONPATH="${REPO_ROOT}" "${PY}" -m "${SERVER_MODULE}" 2>/dev/null \
  | "${PY}" -c '
import json, sys
for line in sys.stdin:
    frame = json.loads(line)
    if frame.get("id") == 2:
        names = [t["name"] for t in frame["result"]["tools"]]
        print(f"  {len(names)} tools: " + ", ".join(names))
'
)

if command -v opencode >/dev/null 2>&1; then
  echo
  echo "--- opencode $(opencode --version) sees it ---"
  (cd "${REPO_ROOT}" && opencode mcp list 2>&1 | sed 's/^/  /')
else
  echo
  echo "  opencode is not on PATH. Install it with one of:"
  echo "    npm install -g opencode-ai      # no sudo if npm prefix is user-owned"
  echo "    brew install sst/tap/opencode"
  echo "    curl -fsSL https://opencode.ai/install | bash"
fi
