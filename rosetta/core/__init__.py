"""rosetta.core -- the verifier. The only module that touches YottaDB.

Everything else in the project talks through the frozen contract in
:mod:`rosetta.core.interface`. Import the contract types from here.

    from rosetta.core import ExecSpec, verify_equivalence

    report = verify_equivalence(
        "PRCHUEI", baseline_src, mutated_src,
        [ExecSpec(routine="PRCHUEI", entry="$$VALIDUEI", args=["ZQGGH7C1MJM3"])],
    )
"""

from .interface import (
    Divergence,
    DivergenceKind,
    ExecResult,
    ExecSpec,
    VerifyReport,
)
from .analysis import (
    CapturePlan,
    CaptureTier,
    RoutineFacts,
    RoutineRejected,
    find_tp_commands,
    parse,
    plan_capture,
    reject_if_unsafe,
)
from .config import DEFAULT_CONFIG, KILLED, CoreConfig
from .runtime import (
    Runtime,
    TransactionTooBig,
    VoidExecution,
    clean_state,
    diff_results,
    execute,
    get_runtime,
    load_routine,
    restore,
    shutdown,
    snapshot,
    verify_equivalence,
)
from .worker import MWorker, WorkerDied, WorkerError

__all__ = [
    "CapturePlan",
    "CaptureTier",
    "CoreConfig",
    "DEFAULT_CONFIG",
    "Divergence",
    "DivergenceKind",
    "ExecResult",
    "ExecSpec",
    "KILLED",
    "MWorker",
    "RoutineFacts",
    "RoutineRejected",
    "Runtime",
    "TransactionTooBig",
    "VerifyReport",
    "VoidExecution",
    "WorkerDied",
    "WorkerError",
    "clean_state",
    "diff_results",
    "execute",
    "find_tp_commands",
    "get_runtime",
    "load_routine",
    "parse",
    "plan_capture",
    "reject_if_unsafe",
    "restore",
    "shutdown",
    "snapshot",
    "verify_equivalence",
]
