"""The local web surface. One process, no dependencies, loopback only.

`rosetta gui` serves the views described in docs/WORKFLOWS.md over the same
:mod:`rosetta.workflow` generators the CLI consumes, so a verdict reached in a
browser and a verdict reached in a terminal are the same verdict.

Three properties this package must keep, because the deployment story depends
on them:

  * **Loopback only.** The server binds 127.0.0.1 and refuses anything else.
    A verification tool that listens on a network interface inside a customer
    enclave is a finding, not a feature.
  * **No dependencies.** Python's standard library and hand-written CSS. The
    GUI has to open on a machine that cannot reach a package index.
  * **No new truth.** Every number rendered comes from a workflow or a results
    file. The front end computes nothing it could get wrong.
"""

from __future__ import annotations

__all__ = ["serve"]


def serve(*args, **kwargs):  # pragma: no cover - thin re-export
    from rosetta.gui.server import serve as _serve

    return _serve(*args, **kwargs)
