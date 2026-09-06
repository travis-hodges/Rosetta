"""Read-only FileMan export against an explicitly selected runtime.

This build-time adapter lives inside core so the MCP layer never launches M.
The transaction frame rolls back if the process ends or errors.
"""
from __future__ import annotations

import os
import subprocess
from typing import Any

def _run_m(spec: Any, script: str, timeout_s: float = 300.0) -> str:
    """Pipe an M script into ``mumps -direct`` in the container; return stdout.

    Direct mode is used deliberately: it needs no routine written into the
    container's ``/home/vehu/r``, so the extraction leaves no trace.
    """
    cmd = [
        os.environ.get("ROSETTA_DOCKER", "docker"), "exec", "-i",
        "-e", f"gtm_dist={spec.gtm_dist}",
        "-e", f"gtmgbldir={spec.gtmgbldir}",
        spec.name, spec.mumps, "-direct",
    ]
    proc = subprocess.run(
        cmd, input="TSTART *:SERIAL\n" + script, capture_output=True, text=True, timeout=timeout_s
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"mumps -direct failed (rc={proc.returncode}) in container "
            f"{spec.name!r}: {proc.stderr.strip()[:500]}"
        )
    if proc.stderr.strip():
        raise RuntimeError(f"FileMan export reported an M error: {proc.stderr.strip()[:500]}")
    return proc.stdout


