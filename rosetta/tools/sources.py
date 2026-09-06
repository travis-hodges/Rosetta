"""Local MUMPS corpus with an explicit, optional container source fallback.

ROSETTA_CORPUS_DIR selects user-supplied .m files. The bundled VA corpus is an
example default; no VA container is contacted unless ROSETTA_TOOLS_CONTAINER
is set. Container I/O lives in rosetta.core.source_io.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["RoutineNotFound", "RoutineSource", "RoutineStore", "default_store"]

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS_DIR = _REPO_ROOT / "data" / "routines"

#: Container facts, per PROJECT.md section 14.
DEFAULT_CONTAINER = None
DEFAULT_ROUTINE_DIR = "/home/vehu/r"

_NAME_CHARS = set("%ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


class _Unset:
    """Sentinel: ``container=None`` means *disabled*, omitted means *use env*."""


UNSET = _Unset()


class RoutineNotFound(LookupError):
    """Raised when a routine is in neither the corpus nor the container."""


@dataclass(frozen=True)
class RoutineSource:
    """Source text plus where it came from."""

    name: str
    source: str
    origin: str          # "corpus" | "container"
    path: str
    lines: int

    @property
    def in_corpus(self) -> bool:
        return self.origin == "corpus"


def normalise_name(name: str) -> str:
    """Uppercase and validate a routine name. ``"xlfdt"`` -> ``"XLFDT"``."""
    n = (name or "").strip().upper()
    if not n:
        raise ValueError("empty routine name")
    if not re.fullmatch(r"%?[A-Z][A-Z0-9]*", n):
        raise ValueError(
            f"not a valid MUMPS routine name: {name!r} "
            "(letters, digits, optional leading %)"
        )
    return n


def filename_for(name: str) -> str:
    """``"%DTC"`` -> ``"_DTC.m"``; ``"XLFDT"`` -> ``"XLFDT.m"``."""
    n = normalise_name(name)
    return ("_" + n[1:] if n.startswith("%") else n) + ".m"


def name_for_filename(filename: str) -> str:
    """``"_DTC.m"`` -> ``"%DTC"``. Inverse of :func:`filename_for`."""
    stem = Path(filename).stem
    return "%" + stem[1:] if stem.startswith("_") else stem


class RoutineStore:
    """Read-only routine catalogue over the corpus, with container fallback."""

    def __init__(
        self,
        corpus_dir: Path | None = None,
        container: str | None | _Unset = UNSET,
        routine_dir: str = DEFAULT_ROUTINE_DIR,
        docker_timeout_s: float = 20.0,
    ) -> None:
        configured = os.environ.get("ROSETTA_CORPUS_DIR")
        self.corpus_dir = Path(corpus_dir or configured or DEFAULT_CORPUS_DIR).expanduser().resolve()
        if (corpus_dir is not None or configured) and not self.corpus_dir.is_dir():
            raise ValueError(f"routine corpus directory does not exist: {self.corpus_dir}")
        env = os.environ.get("ROSETTA_TOOLS_CONTAINER")
        if not isinstance(container, _Unset):
            self.container: str | None = container
        elif env is None:
            self.container = DEFAULT_CONTAINER
        elif env.lower() in ("off", "none", "0", ""):
            self.container = None
        else:
            self.container = env
        self.routine_dir = os.environ.get("ROSETTA_TOOLS_ROUTINE_DIR", routine_dir)
        self.docker_timeout_s = docker_timeout_s
        self._corpus_names: list[str] | None = None
        self._container_names: list[str] | None = None
        self._container_failed = False

    # -- corpus ------------------------------------------------------------

    def corpus_names(self) -> list[str]:
        """Sorted routine names present in ``data/routines/``."""
        if self._corpus_names is None:
            if self.corpus_dir.is_dir():
                self._corpus_names = sorted(
                    name_for_filename(p.name) for p in self.corpus_dir.glob("*.m")
                )
            else:
                self._corpus_names = []
        return self._corpus_names

    # -- container ---------------------------------------------------------

    def _docker(self, args: list[str]) -> str:
        from rosetta.core.source_io import container_files
        return container_files(self.container or "", args, self.docker_timeout_s)

    def container_names(self) -> list[str]:
        """Routine names in the container, or ``[]`` if it is unreachable."""
        if self.container is None or self._container_failed:
            return []
        if self._container_names is None:
            try:
                out = self._docker(["ls", self.routine_dir])
            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                raise RoutineNotFound(f"configured container {self.container!r} is unavailable: {exc}") from exc
            self._container_names = sorted(
                name_for_filename(line.strip())
                for line in out.splitlines()
                if line.strip().endswith(".m")
            )
        return self._container_names

    @property
    def container_available(self) -> bool:
        return bool(self.container_names())

    # -- public API --------------------------------------------------------

    def list(
        self,
        pattern: str | None = None,
        include_container: bool = False,
        limit: int = 500,
    ) -> tuple[list[str], int]:
        """Names matching a case-insensitive glob. Returns ``(names, total)``.

        ``pattern`` is shell-glob (``"DG*"``, ``"*UTL*"``). A pattern with no
        glob metacharacters is treated as a substring match, because that is
        what a model almost always means.
        """
        names = list(self.corpus_names())
        if include_container:
            extra = set(self.container_names()) - set(names)
            names = sorted(set(names) | extra)
        if pattern:
            p = pattern.strip().upper()
            if any(ch in p for ch in "*?["):
                names = [n for n in names if fnmatch.fnmatchcase(n, p)]
            else:
                names = [n for n in names if p in n]
        return names[:limit], len(names)

    def read(self, name: str) -> RoutineSource:
        """Return the source of one routine. Raises :class:`RoutineNotFound`."""
        n = normalise_name(name)
        path = self.corpus_dir / filename_for(n)
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            return RoutineSource(
                name=n,
                source=text,
                origin="corpus",
                path=str(path),
                lines=len(text.splitlines()),
            )
        if self.container is not None and not self._container_failed:
            remote = f"{self.routine_dir}/{filename_for(n)}"
            try:
                text = self._docker(["cat", remote])
            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                raise RoutineNotFound(
                    f"routine {n!r} is not in the corpus ({self.corpus_dir}) and "
                    f"the container fallback failed: {exc}. Use list_routines "
                    "to see what is available."
                ) from exc
            return RoutineSource(
                name=n,
                source=text,
                origin="container",
                path=f"{self.container}:{remote}",
                lines=len(text.splitlines()),
            )
        raise RoutineNotFound(
            f"routine {n!r} is not in the benchmark corpus ({self.corpus_dir}) "
            "and the container fallback is disabled "
            "(ROSETTA_TOOLS_CONTAINER=off). Use list_routines to see what "
            "is available."
        )

    def try_read(self, name: str) -> RoutineSource | None:
        try:
            return self.read(name)
        except (RoutineNotFound, ValueError):
            return None


_DEFAULT: RoutineStore | None = None


def default_store() -> RoutineStore:
    """Process-wide routine store."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = RoutineStore()
    return _DEFAULT
