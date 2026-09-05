"""Line protocol shared with ``m/ROSWRK.m``.

    request  : KEY<TAB>VALUE lines, terminated by a bare line ``END``
    response : KEY<TAB>VALUE lines, terminated by a bare line ``END``

Values are percent-escaped so a value can never contain a TAB, a newline, or
the ``=`` used to pair fields. The escape set is deliberately tiny -- five
characters -- because the M-side decoder is a hand-written loop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TAB = "\t"

_ESCAPES = {
    "%": "%25",
    "=": "%3D",
    "\t": "%09",
    "\n": "%0A",
    "\r": "%0D",
}
_NEEDS_ESCAPE = re.compile(r"[%=\t\n\r]")
_ESCAPED = re.compile(r"%([0-9A-Fa-f]{2})")


def escape(value: str) -> str:
    """Encode a value for one protocol line."""
    if not _NEEDS_ESCAPE.search(value):
        return value
    return _NEEDS_ESCAPE.sub(lambda m: _ESCAPES[m.group(0)], value)


def unescape(value: str) -> str:
    """Decode one protocol line value."""
    if "%" not in value:
        return value
    return _ESCAPED.sub(lambda m: chr(int(m.group(1), 16)), value)


@dataclass
class Request:
    """An ordered multimap of protocol fields."""

    pairs: list[tuple[str, str]] = field(default_factory=list)

    def add(self, key: str, value: object) -> "Request":
        self.pairs.append((key, str(value)))
        return self

    def encode(self) -> str:
        lines = [f"{k}{TAB}{escape(v)}" for k, v in self.pairs]
        lines.append("END")
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class Response:
    """Decoded response. ``multi`` preserves repeated keys in arrival order."""

    multi: list[tuple[str, str]]

    @classmethod
    def parse(cls, lines: list[str]) -> "Response":
        out: list[tuple[str, str]] = []
        for line in lines:
            if not line:
                continue
            key, _, raw = line.partition(TAB)
            out.append((key, unescape(raw)))
        return cls(multi=out)

    def get(self, key: str, default: str = "") -> str:
        for k, v in self.multi:
            if k == key:
                return v
        return default

    def get_all(self, key: str) -> list[str]:
        return [v for k, v in self.multi if k == key]

    def get_int(self, key: str, default: int = 0) -> int:
        raw = self.get(key, "")
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return default

    @property
    def status(self) -> str:
        return self.get("STATUS", "ERR")

    @property
    def ok(self) -> bool:
        return self.status == "OK"
