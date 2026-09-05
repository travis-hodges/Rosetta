"""rosetta.mutate -- manufacture benchmark tasks by breaking known-good VistA code.

The mutation *is* the ground truth: take a routine that works, inject one
mechanical defect, and the corrected version is by construction the original.
See docs/PROJECT.md sections 6 (operators) and 9 (validity rules and input
suites).

Typical use::

    from rosetta.mutate import parse_routine, mutate_routine
    from rosetta.mutate.generate import generate_for_routine
    from rosetta.mutate.verify import CoreVerifier

    result = generate_for_routine("PRCHUEI", src, CoreVerifier())

Module map:

``lex``
    Offset-preserving MUMPS line/command model. Wraps the string masker and
    depth-zero splitter from :mod:`rosetta.bench.select`.
``operators``
    The eight mutation operators, each tagged easy/medium/hard.
``cases``
    Input-suite generation from static analysis, boundary values and (stubbed)
    real global samples.
``verify``
    The seam onto ``rosetta.core.verify_equivalence``.
``generate``
    Candidate -> validated ``MutationTask``.
"""

from rosetta.mutate.lex import (
    Routine,
    assert_no_tp_command,
    has_tp_command,
    parse_routine,
)
from rosetta.mutate.operators import (
    OPERATOR_NAMES,
    OPERATORS,
    Difficulty,
    Mutation,
    mutate_routine,
)

__all__ = [
    "Routine",
    "parse_routine",
    "has_tp_command",
    "assert_no_tp_command",
    "Mutation",
    "Difficulty",
    "OPERATORS",
    "OPERATOR_NAMES",
    "mutate_routine",
]
