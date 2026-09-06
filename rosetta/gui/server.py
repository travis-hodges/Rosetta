"""The HTTP server. Standard library, loopback, no dependencies.

Two things here are load-bearing rather than incidental:

  * The bind address is forced to a loopback interface. A verification tool
    listening on a routable interface inside a customer enclave is a security
    finding, and making that configurable would be an invitation to file one.
  * Static files are resolved and then re-checked against the asset directory,
    so a crafted path cannot read outside it.
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import parse_qs, urlparse

from rosetta.gui.api import Api, ApiError

STATIC = Path(__file__).resolve().parent / "static"
LOOPBACK = ("127.0.0.1", "::1", "localhost")
MAX_BODY = 4 * 1024 * 1024  # a MUMPS routine is kilobytes; this is generous

TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
}

__all__ = ["serve", "main"]


class Handler(BaseHTTPRequestHandler):
    server_version = "rosetta"
    protocol_version = "HTTP/1.1"
    api: Api

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:
        # One line per request on stderr, so `rosetta gui` in a terminal shows
        # what the page is doing without a flag.
        sys.stderr.write(f"  {self.command} {self.path.split('?')[0]} "
                         f"{args[1] if len(args) > 1 else ''}\n")

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # The page loads nothing remote and must not be embedded elsewhere.
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _read_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError("Content-Length is not a number") from None
        if length > MAX_BODY:
            raise ApiError("request body too large", 413)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(f"body is not JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise ApiError("body must be a JSON object")
        return body

    # -- routing -----------------------------------------------------------

    def _dispatch(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            try:
                body = self._read_body() if self.command in ("POST", "DELETE") else {}
                status, payload = self.api.handle(self.command, path, query, body)
            except ApiError as exc:
                self._json(exc.status, {"error": str(exc), **exc.data})
            except Exception as exc:  # fail loudly, and visibly in the page
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            else:
                self._json(status, payload)
            return
        self._static(path)

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (STATIC / rel).resolve()
        # Resolve first, then confirm containment: this is what stops
        # `/../../etc/passwd` and symlink escapes alike.
        if not target.is_file() or STATIC.resolve() not in target.parents:
            self._send(404, b"not found\n", "text/plain; charset=utf-8")
            return
        ctype = TYPES.get(target.suffix, "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)

    do_GET = _dispatch
    do_HEAD = _dispatch
    do_POST = _dispatch
    do_DELETE = _dispatch


def serve(host: str = "127.0.0.1", port: int = 7391, open_browser: bool = True) -> int:
    """Run until interrupted. Returns a process exit code."""
    if host not in LOOPBACK:
        print(f"refusing to bind {host}: the GUI is loopback-only, by design.",
              file=sys.stderr)
        print("Rosetta is built to run inside an enclave; a verification tool "
              "listening on a\nroutable interface is a finding, not a feature.",
              file=sys.stderr)
        return 2
    if not (STATIC / "index.html").is_file():
        print(f"static assets are missing from {STATIC}", file=sys.stderr)
        return 1

    Handler.api = Api()
    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        print(f"could not bind {host}:{port}: {exc}", file=sys.stderr)
        print(f"       rosetta gui --port {port + 1}", file=sys.stderr)
        return 1

    url = f"http://{host}:{port}/"
    print(f"Rosetta GUI on {url}")
    print("  loopback only. ctrl-c to stop.\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # a headless box must not fail to serve
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        httpd.server_close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rosetta gui", description=__doc__)
    ap.add_argument("--port", type=int, default=7391)
    ap.add_argument("--host", default="127.0.0.1", help="loopback addresses only")
    ap.add_argument("--no-open", action="store_true", help="do not open a browser")
    args = ap.parse_args(list(argv) if argv is not None else None)
    return serve(args.host, args.port, open_browser=not args.no_open)


if __name__ == "__main__":
    raise SystemExit(main())
