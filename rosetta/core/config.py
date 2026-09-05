"""Runtime configuration for the YottaDB verification environment.

Every value can be overridden with an environment variable so a different
container or instance name does not require a code change. Defaults match what
`scripts/bootstrap.sh` builds.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class CoreConfig:
    """Where YottaDB lives and how we talk to it."""

    container: str = field(default_factory=lambda: _env("ROSETTA_CONTAINER", "rosetta-verify"))
    instance: str = field(default_factory=lambda: _env("ROSETTA_INSTANCE", "vehu"))
    docker: str = field(default_factory=lambda: _env("ROSETTA_DOCKER", "docker"))

    #: Seconds to wait for any single worker response before declaring a timeout
    #: and killing the process. Per-case timeouts come from ``ExecSpec.timeout_s``.
    default_timeout_s: float = 20.0

    #: Seconds to wait for the worker to print its banner after spawn.
    startup_timeout_s: float = 60.0

    #: Cap on nodes collected per watched global root in one $QUERY walk.
    max_nodes_per_root: int = 20_000

    #: Subscript depth for the trigger tier. Triggers are per (global, depth).
    trigger_depth: int = 8

    #: A $QUERY walk of a root larger than this is too slow for the hot loop
    #: (a full ^DPT walk is 2-4s), so such roots go to the trigger tier instead.
    query_tier_node_cap: int = 2_000

    @property
    def basedir(self) -> str:
        return f"/home/{self.instance}"

    @property
    def routine_dir(self) -> str:
        return f"{self.basedir}/r"

    @property
    def scratch_dir(self) -> str:
        return f"{self.basedir}/tmp/rosetta"

    @property
    def snapshot_dir(self) -> str:
        return f"{self.scratch_dir}/snapshots"

    @property
    def env_file(self) -> str:
        return f"{self.basedir}/etc/env"

    @property
    def region_manifest(self) -> str:
        return f"{self.basedir}/tmp/rosetta-regions.tsv"


#: Path to the M worker source shipped with this package.
M_WORKER_SOURCE: Path = Path(__file__).with_name("m") / "ROSWRK.m"

#: Global roots the verifier owns. Never part of a routine's observable state.
RESERVED_GLOBALS: frozenset[str] = frozenset({"^ROSLOG", "^ROSTMP", "^ROSSTATE"})

#: Sentinel value the worker reports for a ref the body KILLed.
KILLED = "\x01KILLED"

DEFAULT_CONFIG = CoreConfig()
