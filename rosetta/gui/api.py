"""The JSON API behind the views. Thin on purpose.

Every handler is a lookup, a workflow call, or a file read. There is no
scoring, no averaging and no derived metric in this file: the front end must
not be able to publish a number the CLI cannot reproduce, so anything
statistical is read from `results/summary.json`, which only
`rosetta bench report` writes.

Handlers return ``(status, payload)``. Raising is also fine -- the server
turns an exception into a 500 with the message, because a silent failure in a
verification tool is the one outcome worse than a crash.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rosetta.gui import jobs

REPO_ROOT = Path(__file__).resolve().parents[2]

__all__ = ["Api", "ApiError"]


class ApiError(Exception):
    """A request the client got wrong. Surfaces as a 4xx with this message."""

    def __init__(self, message: str, status: int = 400, **data: Any) -> None:
        super().__init__(message)
        self.status = status
        self.data = data


class Api:
    """Request handlers, holding the one expensive object between calls.

    The tool registry loads the routine corpus and the FileMan dictionary, so
    it is built once and reused. It is created lazily: `rosetta gui` has to
    start and serve its first page on a machine where the container is down.
    """

    def __init__(self) -> None:
        self._registry: Any = None

    @property
    def registry(self) -> Any:
        if self._registry is None:
            from rosetta.tools.tools import ToolRegistry

            self._registry = ToolRegistry()
        return self._registry

    # -- dispatch ----------------------------------------------------------

    def handle(
        self, method: str, path: str, query: dict[str, str], body: dict[str, Any]
    ) -> tuple[int, Any]:
        key = (method, path)
        route = _ROUTES.get(key)
        if route is None:
            raise ApiError(f"no such endpoint: {method} {path}", 404)
        return route(self, query, body)

    # -- read-only, always available --------------------------------------

    def status(self, query: dict[str, str], body: dict[str, Any]) -> tuple[int, Any]:
        """The same facts as `rosetta status`, filesystem only.

        Deliberately does not probe the container: the home view has to render
        instantly and identically whether or not YottaDB is up. `doctor` is
        where the slow truth lives.
        """
        from rosetta import models as registry
        from rosetta.cli import _count_tasks, _published, _split_summary, _traces

        published = _published()
        return 200, {
            "models": [m.to_dict() for m in registry.load()],
            "split": _split_summary(),
            "n_tasks": _count_tasks(),
            "n_traces": len(_traces()),
            "published": published,
            "repo": str(REPO_ROOT),
        }

    def doctor(self, query: dict[str, str], body: dict[str, Any]) -> tuple[int, Any]:
        """Start the slow checks as a job. Probing the container blocks."""

        def produce():
            import sys

            yield {"kind": "check", "name": "python", "ok": sys.version_info >= (3, 11),
                   "detail": sys.version.split()[0]}
            from rosetta.demo.verify import verifier_status

            ok, reason = verifier_status()
            yield {"kind": "check", "name": "verifier", "ok": ok, "detail": reason}
            from rosetta.demo.agents import opencode_available

            ok, reason = opencode_available()
            yield {"kind": "check", "name": "model access", "ok": ok, "detail": reason}

        job = jobs.start("doctor", "environment check", produce)
        return 202, job.to_dict()

    def routines(self, query: dict[str, str], body: dict[str, Any]) -> tuple[int, Any]:
        names, total = self.registry.store.list(query.get("q") or None, limit=60)
        return 200, {"names": names, "total": total}

    def routine(self, query: dict[str, str], body: dict[str, Any]) -> tuple[int, Any]:
        from rosetta.tools.tools import ToolError
        from rosetta.workflow import cases_for

        name = (query.get("name") or "").strip()
        if not name:
            raise ApiError("name is required")
        try:
            name, source, origin = self.registry.read_source(name)
        except ToolError as exc:
            raise ApiError(str(exc), 404, **exc.data) from exc
        cases, case_origin = cases_for(self.registry, name)
        return 200, {
            "routine": name,
            "origin": origin,
            "source": source,
            "lines": len(source.splitlines()),
            "cases_origin": case_origin,
            "n_cases": len(cases) if cases else None,
            "verifiable": bool(cases) or "suite" in case_origin,
        }

    # -- the two workflows that need the verifier --------------------------

    def verify(self, query: dict[str, str], body: dict[str, Any]) -> tuple[int, Any]:
        from rosetta.workflow import cases_for, verify

        routine = str(body.get("routine") or "").strip().upper()
        candidate = body.get("candidate_src")
        if not routine:
            raise ApiError("routine is required")
        if not isinstance(candidate, str) or not candidate.strip():
            raise ApiError("candidate_src must be the complete replacement source")
        baseline = body.get("baseline_src") or None
        reg = self.registry
        cases, case_origin = cases_for(reg, routine)

        def produce():
            yield {"kind": "inputs", "origin": case_origin,
                   "n_cases": len(cases) if cases else None}
            report = verify(routine, candidate, baseline_src=baseline,
                            cases=cases, registry=reg)
            yield {"kind": "report", **report}

        job = jobs.start("verify", f"verify {routine}", produce)
        return 202, job.to_dict()

    def edit(self, query: dict[str, str], body: dict[str, Any]) -> tuple[int, Any]:
        from rosetta import models as registry
        from rosetta.workflow import edit

        routine = str(body.get("routine") or "").strip().upper()
        request = str(body.get("request") or "").strip()
        if not routine or not request:
            raise ApiError("routine and request are both required")
        try:
            attempts = int(body.get("attempts", 3))
        except (TypeError, ValueError):
            raise ApiError("attempts must be a number") from None
        if not 1 <= attempts <= 10:
            raise ApiError("attempts must be between 1 and 10")
        try:
            timeout_s = float(body.get("timeout_s", 1800.0))
        except (TypeError, ValueError):
            raise ApiError("timeout_s must be a number") from None
        if not 30.0 <= timeout_s <= 7200.0:
            raise ApiError("timeout_s must be between 30 and 7200 seconds")
        model = registry.resolve(body.get("model") or None)
        reg = self.registry

        def produce():
            for ev in edit(routine, request, model=model, attempts=attempts,
                           timeout_s=timeout_s, registry=reg):
                yield ev.to_dict()

        job = jobs.start("edit", f"edit {routine}", produce)
        return 202, job.to_dict()

    def job(self, query: dict[str, str], body: dict[str, Any]) -> tuple[int, Any]:
        job = jobs.get(query.get("id", ""))
        if job is None:
            raise ApiError("no such job (it may have been pruned)", 404)
        try:
            since = int(query.get("since", "0"))
        except ValueError:
            since = 0
        return 200, job.to_dict(since=max(0, since))

    # -- models ------------------------------------------------------------

    def models_get(self, query: dict[str, str], body: dict[str, Any]) -> tuple[int, Any]:
        from rosetta import models as registry

        return 200, {"models": [m.to_dict() for m in registry.load()]}

    def models_post(self, query: dict[str, str], body: dict[str, Any]) -> tuple[int, Any]:
        from rosetta import models as registry

        try:
            m = registry.add(
                str(body.get("name", "")).strip(),
                str(body.get("model", "")).strip(),
                str(body.get("notes", "")).strip(),
                replace=bool(body.get("replace")),
            )
        except registry.RegistryError as exc:
            raise ApiError(str(exc), 409) from exc
        return 200, m.to_dict()

    def models_delete(self, query: dict[str, str], body: dict[str, Any]) -> tuple[int, Any]:
        from rosetta import models as registry

        try:
            m = registry.remove(str(query.get("name", "")))
        except registry.RegistryError as exc:
            raise ApiError(str(exc), 404) from exc
        return 200, m.to_dict()

    def model_test(self, query: dict[str, str], body: dict[str, Any]) -> tuple[int, Any]:
        from rosetta import models as registry

        name = str(body.get("name") or "").strip()
        model = registry.resolve(name or None)

        def produce():
            from rosetta.demo.agents import OpenCodeAgent
            from rosetta.workflow import EditTask

            yield {"kind": "asking", "model": model or "opencode default"}
            agent = OpenCodeAgent(model=model, tools_on=False,
                                  cwd=str(REPO_ROOT), max_attempts=1)
            task = EditTask("XLFSTR", "Return the string unchanged. Change nothing else.")
            attempt = agent.propose(task, "XLFSTR ;\n QUIT\n", [])
            if attempt is None:
                raise RuntimeError(f"{model or 'default'} returned nothing")
            # Reachability and output format only. Competence is measured by
            # the benchmark, never by a smoke test, and the view says so.
            yield {"kind": "reachable", "model": model or "opencode default",
                   "lines": len(attempt.candidate_src.splitlines())}

        job = jobs.start("model-test", f"test {name or 'default'}", produce)
        return 202, job.to_dict()

    # -- runs --------------------------------------------------------------

    def runs(self, query: dict[str, str], body: dict[str, Any]) -> tuple[int, Any]:
        """Published results, plus the trace files every number came from.

        Nothing is computed here. `published` is `results/summary.json` exactly
        as `rosetta bench report` wrote it, and the trace list is provenance --
        if the view wants a figure that is not in the summary, the answer is to
        run the report, not to average something in JavaScript.
        """
        from rosetta.bench.trace import TraceFormatError, read_traces

        from rosetta.cli import _published, _traces

        files: list[dict[str, Any]] = []
        for path in _traces():
            entry: dict[str, Any] = {"file": path.name, "models": [],
                                     "conditions": [], "n_records": 0}
            try:
                records = read_traces(path)
            except (TraceFormatError, OSError, ValueError) as exc:
                entry["error"] = str(exc)
                files.append(entry)
                continue
            entry["n_records"] = len(records)
            entry["models"] = sorted({r.model for r in records if r.model})
            entry["conditions"] = sorted({r.condition for r in records if r.condition})
            entry["tasks"] = len({r.task_id for r in records})
            files.append(entry)
        return 200, {"published": _published(), "traces": files,
                     "summary_path": "results/summary.json"}


_ROUTES: dict[tuple[str, str], Any] = {
    ("GET", "/api/status"): Api.status,
    ("POST", "/api/doctor"): Api.doctor,
    ("GET", "/api/routines"): Api.routines,
    ("GET", "/api/routine"): Api.routine,
    ("POST", "/api/verify"): Api.verify,
    ("POST", "/api/edit"): Api.edit,
    ("GET", "/api/job"): Api.job,
    ("GET", "/api/models"): Api.models_get,
    ("POST", "/api/models"): Api.models_post,
    ("DELETE", "/api/models"): Api.models_delete,
    ("POST", "/api/model-test"): Api.model_test,
    ("GET", "/api/runs"): Api.runs,
}
