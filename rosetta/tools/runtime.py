"""THE SEAM. Everything in this stream that needs to *run* MUMPS goes here.

Why a seam exists at all
------------------------
PROJECT.md section 11 rule 3: only ``rosetta/core/`` may touch YottaDB. This
package therefore never imports a runtime, never shells to ``docker`` for
execution, and never opens an M process. It declares the shape of the thing it
needs — :class:`ExecutionBackend` — and asks :func:`get_backend` for one.

Three backends
--------------
``CoreBackend``
    Adapts ``rosetta.core`` to the protocol. Resolved by *deferred* import
    (``importlib``) at first use, so ``rosetta.tools`` imports cleanly and all
    five static tools work today, before ``rosetta.core`` exists. The moment
    core lands and exposes ``execute`` / ``load_routine`` /
    ``verify_equivalence`` per the frozen contract, the three execution-backed
    tools light up with no edit here.
``UnavailableBackend``
    The default when core is absent. Every call raises
    :class:`BackendUnavailable`, which the server turns into a well-formed
    JSON-RPC tool error explaining exactly what is missing. It never crashes
    the server and never hangs the pipe.
``FakeBackend``
    Deterministic in-memory stand-in for tests. Not wired in automatically.

Selection is by ``ROSETTA_TOOLS_BACKEND``:

===============  ===========================================================
``auto``         (default) use core if importable, else unavailable
``core``         require core; a failed import is an error, not a fallback
``none``         force unavailable — useful to prove the static tools stand alone
===============  ===========================================================

Only ``rosetta.core.interface`` is imported statically, per the stream rules;
it is the frozen contract and owns every type crossing this boundary.
"""

from __future__ import annotations

import importlib
import os
import time
from dataclasses import dataclass, field as dc_field
from typing import Protocol, runtime_checkable

from rosetta.core.interface import (
    Divergence,
    ExecResult,
    ExecSpec,
    VerifyReport,
)

__all__ = [
    "BackendUnavailable",
    "ExecutionBackend",
    "CoreBackend",
    "UnavailableBackend",
    "FakeBackend",
    "get_backend",
    "set_backend",
    "backend_status",
]

#: Names ``rosetta.core`` must expose for :class:`CoreBackend` to bind.
REQUIRED_CORE_ATTRS = ("execute", "load_routine", "verify_equivalence")


class BackendUnavailable(RuntimeError):
    """The execution backend (``rosetta.core``) is not available.

    Raised — never swallowed — by every execution path when core is missing, so
    a benchmark number can never be produced from a silently skipped run.
    """


@runtime_checkable
class ExecutionBackend(Protocol):
    """The only shape ``rosetta.tools`` needs from the verifier.

    Deliberately narrow: three methods, all typed with frozen-contract
    dataclasses. Snapshot/restore and ``clean_state()`` isolation are *inside*
    the implementation — the tool layer must not be able to run anything
    unisolated.
    """

    name: str

    def load_routine(self, name: str, source: str) -> None:
        """Compile ``source`` as routine ``name`` in the environment."""

    def execute(self, spec: ExecSpec) -> ExecResult:
        """Run one spec, isolated. Must snapshot/roll back around the body."""

    def verify_equivalence(
        self,
        routine: str,
        baseline_src: str,
        candidate_src: str,
        cases: list[ExecSpec],
    ) -> VerifyReport:
        """Compare two versions of one routine over an input suite."""


# --------------------------------------------------------------------------


@dataclass
class UnavailableBackend:
    """Default backend. Fails loudly with an actionable message."""

    name: str = "unavailable"
    reason: str = "rosetta.core is not importable yet"

    def _fail(self, op: str) -> None:
        raise BackendUnavailable(
            f"cannot {op}: {self.reason}. The five static tools "
            "(list_routines, read_routine, parse_routine, call_graph, "
            "resolve_global) do not need a runtime and are unaffected. "
            "Execution-backed tools require rosetta.core to expose "
            f"{', '.join(REQUIRED_CORE_ATTRS)} per rosetta/core/interface.py."
        )

    def load_routine(self, name: str, source: str) -> None:
        self._fail(f"load routine {name!r}")

    def execute(self, spec: ExecSpec) -> ExecResult:
        self._fail(f"execute {spec.routine!r}")
        raise AssertionError("unreachable")

    def verify_equivalence(
        self, routine: str, baseline_src: str, candidate_src: str,
        cases: list[ExecSpec],
    ) -> VerifyReport:
        self._fail(f"verify {routine!r}")
        raise AssertionError("unreachable")


@dataclass
class CoreBackend:
    """Adapter onto ``rosetta.core``. Bound lazily; see the module docstring."""

    module: object
    name: str = "core"

    @classmethod
    def resolve(cls) -> "CoreBackend":
        """Import ``rosetta.core`` and check it satisfies the contract."""
        mod = importlib.import_module("rosetta.core")
        missing = [a for a in REQUIRED_CORE_ATTRS if not hasattr(mod, a)]
        if missing:
            raise BackendUnavailable(
                "rosetta.core is importable but does not yet export "
                f"{', '.join(missing)}"
            )
        return cls(module=mod)

    def load_routine(self, name: str, source: str) -> None:
        self.module.load_routine(name, source)  # type: ignore[attr-defined]

    def execute(self, spec: ExecSpec) -> ExecResult:
        return self.module.execute(spec)  # type: ignore[attr-defined]

    def verify_equivalence(
        self, routine: str, baseline_src: str, candidate_src: str,
        cases: list[ExecSpec],
    ) -> VerifyReport:
        return self.module.verify_equivalence(  # type: ignore[attr-defined]
            routine, baseline_src, candidate_src, cases
        )


@dataclass
class FakeBackend:
    """Deterministic stand-in for tests. Never selected automatically.

    ``outputs`` maps ``(routine, entry)`` to the stdout each version produces:
    ``{("T", "MAIN"): {"baseline": "1", "candidate": "0"}}``. Anything not
    listed returns empty output and no globals.
    """

    outputs: dict[tuple[str, str | None], dict[str, str]] = dc_field(default_factory=dict)
    globals_by_version: dict[str, dict[str, str]] = dc_field(default_factory=dict)
    name: str = "fake"
    _loaded: dict[str, str] = dc_field(default_factory=dict)
    _version: str = "baseline"

    def load_routine(self, name: str, source: str) -> None:
        self._loaded[name.upper()] = source

    def execute(self, spec: ExecSpec) -> ExecResult:
        start = time.perf_counter()
        out = self.outputs.get((spec.routine.upper(), spec.entry), {})
        return ExecResult(
            stdout=out.get(self._version, ""),
            error=None,
            globals_out=dict(self.globals_by_version.get(self._version, {})),
            duration_ms=int((time.perf_counter() - start) * 1000),
        )

    def verify_equivalence(
        self, routine: str, baseline_src: str, candidate_src: str,
        cases: list[ExecSpec],
    ) -> VerifyReport:
        divergences: list[Divergence] = []
        for i, case in enumerate(cases):
            self._version = "baseline"
            base = self.execute(case)
            self._version = "candidate"
            cand = self.execute(case)
            if base.stdout != cand.stdout:
                divergences.append(
                    Divergence(kind="output", ref="stdout", expected=base.stdout,
                               actual=cand.stdout, case_index=i)
                )
            for ref in sorted(set(base.globals_out) | set(cand.globals_out)):
                e, a = base.globals_out.get(ref, ""), cand.globals_out.get(ref, "")
                if e != a:
                    divergences.append(
                        Divergence(kind="global", ref=ref, expected=e, actual=a,
                                   case_index=i)
                    )
        return VerifyReport(
            equivalent=not divergences,
            divergences=divergences,
            n_cases=len(cases),
            n_diverged=len({d.case_index for d in divergences}),
        )


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------

_BACKEND: ExecutionBackend | None = None


def set_backend(backend: ExecutionBackend | None) -> None:
    """Inject a backend (tests, or ``rosetta.core`` wiring itself in)."""
    global _BACKEND
    _BACKEND = backend


def get_backend() -> ExecutionBackend:
    """Return the process backend, resolving it on first call."""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    mode = os.environ.get("ROSETTA_TOOLS_BACKEND", "auto").strip().lower()
    if mode == "none":
        _BACKEND = UnavailableBackend(
            reason="backend disabled by ROSETTA_TOOLS_BACKEND=none"
        )
        return _BACKEND
    try:
        _BACKEND = CoreBackend.resolve()
    except (ImportError, BackendUnavailable, AttributeError) as exc:
        if mode == "core":
            raise BackendUnavailable(
                f"ROSETTA_TOOLS_BACKEND=core but rosetta.core is unusable: {exc}"
            ) from exc
        _BACKEND = UnavailableBackend(reason=str(exc))
    return _BACKEND


def backend_status() -> dict[str, object]:
    """Human/machine readable seam state, surfaced by ``initialize``."""
    backend = get_backend()
    available = not isinstance(backend, UnavailableBackend)
    return {
        "backend": getattr(backend, "name", type(backend).__name__),
        "execution_available": available,
        "reason": "" if available else getattr(backend, "reason", ""),
        "mode": os.environ.get("ROSETTA_TOOLS_BACKEND", "auto"),
        "requires": list(REQUIRED_CORE_ATTRS),
    }
