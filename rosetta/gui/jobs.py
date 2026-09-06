"""Background jobs, because verification is slow and a browser is not.

A verify is seconds to minutes; a full edit loop drives a model three times
and can run for many minutes. Neither fits in a request, so the API starts a
job and the page polls it.

Design constraints that shaped this:

  * **Events accumulate, they do not replace.** The Edit view has to show
    attempt 1's rejected diff next to attempt 2's, because the sequence *is*
    the demonstration. A job that only reported its latest state would throw
    away the interesting part.
  * **A crashed job is a visible failure.** The worker catches everything and
    records it as a ``failed`` state with the message. A job that vanished
    would look identical to one still running.
  * **Bounded memory.** Jobs are capped and the oldest finished ones are
    dropped, so a long session cannot grow without limit.
"""

from __future__ import annotations

import itertools
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

MAX_JOBS = 64

_counter = itertools.count(1)
_lock = threading.Lock()
_jobs: "dict[str, Job]" = {}

__all__ = ["Job", "get", "start", "prune"]


@dataclass
class Job:
    """One running or finished workflow."""

    id: str
    kind: str
    label: str
    state: str = "running"  # running | done | failed
    events: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None

    def to_dict(self, since: int = 0) -> dict[str, Any]:
        """Serialise, sending only events the client has not seen.

        ``since`` is an event count, not an index into a mutable list, which
        is what makes repeated polling safe while the worker is appending.
        Taken under the lock so a poll cannot catch a half-written terminal
        state -- a client that saw ``failed`` without the failure event would
        show a job that stopped for no stated reason.
        """
        with _lock:
            return self._snapshot(since)

    def _snapshot(self, since: int) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "state": self.state,
            "error": self.error,
            "n_events": len(self.events),
            "events": self.events[since:],
            "elapsed_ms": int(((self.finished or time.time()) - self.started) * 1000),
        }


def start(
    kind: str,
    label: str,
    produce: Callable[[], Iterator[dict[str, Any]]],
) -> Job:
    """Run ``produce`` on a worker thread, collecting what it yields."""
    job = Job(id=f"{kind}-{next(_counter)}", kind=kind, label=label)
    with _lock:
        _jobs[job.id] = job
        prune()

    def run() -> None:
        try:
            for event in produce():
                with _lock:
                    job.events.append(event)
            with _lock:
                job.state = "done"
                job.finished = time.time()
        except Exception as exc:  # a hidden failure is worse than a loud one
            with _lock:
                # Append before flipping state: `follow()` stops polling the
                # moment the state is terminal, so an event recorded after
                # that flip would never reach the page.
                job.events.append({
                    "kind": "failed",
                    "message": str(exc),
                    "traceback": traceback.format_exc(limit=6),
                })
                job.error = f"{type(exc).__name__}: {exc}"
                job.finished = time.time()
                job.state = "failed"

    threading.Thread(target=run, name=f"rosetta-{job.id}", daemon=True).start()
    return job


def get(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def prune() -> None:
    """Drop the oldest finished jobs once the table is full. Caller holds the lock."""
    if len(_jobs) <= MAX_JOBS:
        return
    finished = sorted(
        (j for j in _jobs.values() if j.state != "running"),
        key=lambda j: j.finished or j.started,
    )
    for job in finished[: len(_jobs) - MAX_JOBS]:
        _jobs.pop(job.id, None)
