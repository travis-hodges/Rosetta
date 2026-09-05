"""The verification seam.

``rosetta.core`` owns the only code that may touch YottaDB, and it is being
built in parallel with this module. Everything here talks to the frozen
contract's :func:`rosetta.core.interface.verify_equivalence` signature through
one small :class:`Verifier` protocol, so the mutation generator is fully
testable today and wiring the real verifier later is a one-line change at the
call site.

Three implementations ship:

:class:`CoreVerifier`
    The real one. Resolves ``rosetta.core.verify_equivalence`` lazily at first
    call and raises loudly if core is not ready. **Use this for anything whose
    numbers will be published.**
:class:`StaticVerifier`
    A local stub that executes nothing. It admits every textual change. It is
    NOT a semantic oracle -- it cannot tell a killable mutant from an
    equivalent one -- and exists only so the generation pipeline can be run and
    tested without a container. Tasks produced with it are stamped
    ``validated_by="static-stub"`` and must never be scored.
:class:`RecordingVerifier`
    Test double that wraps another verifier and records every call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable

from rosetta.core.interface import Divergence, ExecSpec, VerifyReport

__all__ = [
    "Verifier",
    "CoreVerifier",
    "StaticVerifier",
    "RecordingVerifier",
    "CoreNotAvailable",
    "resolve_verifier",
]


class CoreNotAvailable(RuntimeError):
    """Raised when the real verifier is requested but ``rosetta.core`` is not ready."""


@runtime_checkable
class Verifier(Protocol):
    """Anything that can decide behavioural equivalence of two routine versions.

    Mirrors :func:`rosetta.core.interface.verify_equivalence` exactly. Keep it
    that way -- the whole point of the seam is that the real implementation
    drops in without an adapter.
    """

    def verify_equivalence(
        self,
        routine: str,
        baseline_src: str,
        candidate_src: str,
        cases: list[ExecSpec],
    ) -> VerifyReport:
        ...


@dataclass
class CoreVerifier:
    """Adapter onto the real ``rosetta.core`` verifier.

    Resolution is lazy and deliberately unforgiving: if ``rosetta.core`` does
    not yet export a working ``verify_equivalence``, this raises
    :class:`CoreNotAvailable` rather than degrading to a stub. A silent
    downgrade here would make every benchmark number meaningless.
    """

    name: str = "rosetta.core"
    _fn: Callable[..., VerifyReport] | None = field(default=None, repr=False)

    def _resolve(self) -> Callable[..., VerifyReport]:
        if self._fn is not None:
            return self._fn
        try:
            import rosetta.core as core  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - depends on sibling stream
            raise CoreNotAvailable(f"rosetta.core is not importable: {exc}") from exc
        # Only the package-level export counts. `rosetta.core.interface` holds
        # signatures with empty bodies; calling one returns None and would look
        # like a verifier that never finds a divergence -- the single worst
        # silent failure available to this project.
        fn = getattr(core, "verify_equivalence", None)
        if fn is None:
            raise CoreNotAvailable(
                "rosetta.core does not export verify_equivalence yet. "
                "Pass verifier=StaticVerifier() to run the pipeline "
                "unvalidated, and do not publish the result."
            )
        self._fn = fn
        return fn

    def verify_equivalence(
        self,
        routine: str,
        baseline_src: str,
        candidate_src: str,
        cases: list[ExecSpec],
    ) -> VerifyReport:
        return self._resolve()(routine, baseline_src, candidate_src, cases)


@dataclass
class StaticVerifier:
    """Local stub. Executes nothing; admits every textual difference.

    This is a placeholder for the real oracle, not an approximation of it. It
    returns ``equivalent=False`` with a single ``kind="output"`` divergence
    whose ``ref`` is ``"static-stub"``, so any downstream consumer that
    inspects divergences can see immediately that no execution happened.

    Use it to exercise the generation pipeline. Never to build a benchmark.
    """

    name: str = "static-stub"

    def verify_equivalence(
        self,
        routine: str,
        baseline_src: str,
        candidate_src: str,
        cases: list[ExecSpec],
    ) -> VerifyReport:
        if baseline_src == candidate_src:
            return VerifyReport(
                equivalent=True, divergences=[], n_cases=len(cases), n_diverged=0
            )
        return VerifyReport(
            equivalent=False,
            divergences=[
                Divergence(
                    kind="output",
                    ref="static-stub",
                    expected="<not executed>",
                    actual="<not executed>",
                    case_index=0,
                )
            ],
            n_cases=len(cases),
            n_diverged=len(cases),
        )


@dataclass
class RecordingVerifier:
    """Wrap a verifier and record ``(routine, n_cases)`` for every call."""

    inner: Verifier
    calls: list[tuple[str, int]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"recording({getattr(self.inner, 'name', type(self.inner).__name__)})"

    def verify_equivalence(
        self,
        routine: str,
        baseline_src: str,
        candidate_src: str,
        cases: list[ExecSpec],
    ) -> VerifyReport:
        self.calls.append((routine, len(cases)))
        return self.inner.verify_equivalence(routine, baseline_src, candidate_src, cases)


def resolve_verifier(kind: str) -> Verifier:
    """Map a CLI ``--verifier`` value to an implementation. Fails loudly."""
    if kind == "core":
        return CoreVerifier()
    if kind == "static":
        return StaticVerifier()
    raise ValueError(f"unknown verifier {kind!r}; expected 'core' or 'static'")
