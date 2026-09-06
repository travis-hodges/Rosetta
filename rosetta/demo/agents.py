"""The thing being measured: an agent that proposes a change to a routine.

Two backends, and the difference between them matters enough that every trace
record carries the backend name.

``ScriptedAgent`` (``backend="scripted"``)
    Deterministic, offline, no model. It replays a fixed pair of rewrites --
    the plausible-looking wrong one, then a correct one if and only if it is
    handed verifier feedback. It is the demo's canned path. It is **not** a
    model and a trace it produces is **not** evidence about model behaviour;
    it is evidence about what the verifier does when handed each rewrite.

``RosettaAgent`` (``backend="rosetta"``)
    Shells out to the real Rosetta harness CLI and needs network. This is the
    backend that produces a real measurement. Tools-off runs in a scratch
    directory whose harness config declares no MCP servers, so "no tools"
    is a property of the environment rather than a promise in a prompt.
    It raises :class:`AgentUnavailable` rather than inventing a candidate.

The harness executable, its ``OPENCODE_*`` environment keys and its config
filename keep their upstream names: the binary reads them, so renaming them
would break discovery. Nothing a user reads says anything but Rosetta.

Stdlib only.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from .tasks import DemoTask

__all__ = [
    "Agent",
    "AgentUnavailable",
    "Attempt",
    "RosettaAgent",
    "ScriptedAgent",
    "rosetta_agent_available",
    "unified_diff",
]


class AgentUnavailable(RuntimeError):
    """The backend cannot run here (no binary, no credentials, no network)."""


def unified_diff(baseline: str, candidate: str, routine: str) -> str:
    """Compact unified diff, the form a human reads in the side-by-side."""
    return "".join(
        difflib.unified_diff(
            baseline.splitlines(keepends=True),
            candidate.splitlines(keepends=True),
            fromfile=f"{routine} (baseline)",
            tofile=f"{routine} (candidate)",
            n=1,
        )
    )


@dataclass(frozen=True)
class Attempt:
    """One proposed rewrite plus the agent's own account of it."""

    n: int
    candidate_src: str
    explanation: str
    diff: str

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt": self.n,
            "explanation": self.explanation,
            "diff": self.diff,
            "candidate_sha1": _sha1(self.candidate_src),
        }


def _sha1(text: str) -> str:
    import hashlib

    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class Agent(Protocol):
    """Proposes a candidate, optionally in response to verifier feedback."""

    name: str
    backend: str

    def propose(
        self, task: DemoTask, baseline: str, feedback: Sequence[str]
    ) -> Attempt | None:
        """Return the next candidate, or None to stop. ``feedback`` is the
        verifier text the agent has seen so far; empty means tools are off."""


class ScriptedAgent:
    """Offline stand-in. Replays the task's two recorded rewrites."""

    backend = "scripted"

    def __init__(self, name: str = "scripted") -> None:
        self.name = name

    def propose(
        self, task: DemoTask, baseline: str, feedback: Sequence[str]
    ) -> Attempt | None:
        if not feedback:
            return Attempt(
                n=1,
                candidate_src=task.wrong_candidate(baseline),
                explanation=task.wrong_rationale,
                diff=unified_diff(
                    baseline, task.wrong_candidate(baseline), task.routine
                ),
            )
        if len(feedback) == 1:
            return Attempt(
                n=2,
                candidate_src=task.right_candidate(baseline),
                explanation=task.right_rationale,
                diff=unified_diff(
                    baseline, task.right_candidate(baseline), task.routine
                ),
            )
        return None


def rosetta_agent_available() -> tuple[bool, str]:
    """(usable, reason). The binary is the hard requirement.

    Zero configured credentials is *not* treated as fatal: the harness ships
    hosted models under a built-in provider that answer with an empty
    ``auth.json``. Deciding availability from the credential count would refuse
    to run in exactly the setup that works. If no model can in fact be reached,
    the harness says so and :meth:`RosettaAgent.propose` raises then, with the
    real reason attached.
    """
    binary = shutil.which("opencode")
    if not binary:
        return False, "the Rosetta harness binary is not on PATH"
    try:
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not query the Rosetta harness version: {exc}"
    if proc.returncode != 0:
        return False, f"the Rosetta harness version check exited {proc.returncode}"
    text = proc.stdout + proc.stderr
    if re.search(r"\b0 credentials\b", text):
        return True, f"Rosetta harness at {binary} (no stored credentials; hosted models only)"
    return True, f"Rosetta harness at {binary}"


_FENCE = re.compile(r"```(?:mumps|m|M)?\s*\n(.*?)```", re.DOTALL)

_PROMPT_TOOLS_OFF = """\
You are maintaining MUMPS (M) source from VistA, the US Department of Veterans
Affairs health system. You have the file only. You cannot run anything.

Routine: {routine}
Change request: {request}

Current source of {routine}:
```
{baseline}```

Reply with the COMPLETE replacement source for {routine} in one fenced code
block, then one short paragraph explaining why your change is behaviour
preserving.
"""

_PROMPT_TOOLS_ON = """\
You are maintaining MUMPS (M) source from VistA, the US Department of Veterans
Affairs health system.

You have Rosetta's MCP tools. Use them. In particular `verify_change` runs your
candidate and the original against the real database and reports the specific
divergence; treat a divergence as a bug in your change and repair it until
`verify_change` reports EQUIVALENT.

Routine: {routine}
Change request: {request}

Current source of {routine}:
```
{baseline}```

When you are done, reply with the COMPLETE final source for {routine} in one
fenced code block, then one short paragraph.
{feedback}"""


class RosettaAgent:
    """Drives the real Rosetta harness CLI. Needs credentials and network."""

    backend = "rosetta"

    def __init__(
        self,
        model: str | None = None,
        *,
        tools_on: bool = False,
        cwd: str | None = None,
        timeout_s: float = 900.0,
        max_attempts: int = 3,
        isolated: bool = False,
    ) -> None:
        self.name = f"rosetta:{model or 'default'}"
        self.model = model or os.environ.get("ROSETTA_DEMO_MODEL")
        self.tools_on = tools_on
        self.cwd = cwd
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts
        self.isolated = isolated
        self.transcripts: list[str] = []
        self._isolated: str | None = None

    def _command(self, prompt: str) -> list[str]:
        binary = shutil.which("opencode")
        if not binary:
            raise AgentUnavailable("the Rosetta harness binary is not on PATH")
        cmd = [binary, "run", "--format", "json"]
        if self.isolated or not self.tools_on:
            cmd += ["--agent", "rosetta-eval", "--pure", "--dir", self._workdir()]
        if self.model:
            cmd += ["--model", self.model]
        cmd += ["--", prompt]
        return cmd

    def _workdir(self) -> str:
        """Where the harness runs, which decides which config it reads.

        Tools-off has to mean tools-off. The harness resolves MCP servers from
        the project config in its working directory, so the only honest way to
        withhold them is to run somewhere that does not declare any -- running
        in the repo with a flag would still leave the server one config reload
        away from being live.
        """
        if self.tools_on and not self.isolated:
            return self.cwd or os.getcwd()
        if self._isolated is None:
            self._isolated = tempfile.mkdtemp(prefix="rosetta-tools-off-")
            Path(self._isolated, "opencode.json").write_text(
                json.dumps(self._isolation_config(), indent=2),
                encoding="utf-8",
            )
        return self._isolated

    @staticmethod
    def _isolation_config() -> dict:
        return {
            "$schema": "https://opencode.ai/config.json",
            "instructions": [], "plugin": [],
            "tools": {"*": False}, "permission": {"*": "deny"},
            "default_agent": "rosetta-eval",
            "agent": {"rosetta-eval": {
                "mode": "primary", "description": "Isolated benchmark candidate generation",
                "prompt": "Return only the requested source and explanation. All tools are disabled.",
                "tools": {"*": False}, "permission": {"*": "deny"},
            }},
        }

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.isolated or not self.tools_on:
            # Keep configured providers, but override inherited tool permissions.
            config = json.loads(env.get("OPENCODE_CONFIG_CONTENT", "{}"))
            config.update(self._isolation_config())
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
            env["OPENCODE_DISABLE_CLAUDE_CODE"] = "true"
            # Some CLI builds resolve the project from inherited PWD rather
            # than the subprocess working directory. Keep all three aligned.
            env["PWD"] = self._workdir()
        return env

    def close(self) -> None:
        """Remove the private candidate workspace after a benchmark condition."""
        if self._isolated:
            shutil.rmtree(self._isolated)
            self._isolated = None

    def propose(
        self, task: DemoTask, baseline: str, feedback: Sequence[str]
    ) -> Attempt | None:
        if len(feedback) >= self.max_attempts:
            return None
        usable, reason = rosetta_agent_available()
        if not usable:
            raise AgentUnavailable(reason)

        template = _PROMPT_TOOLS_ON if self.tools_on else _PROMPT_TOOLS_OFF
        if self.isolated:
            template = _PROMPT_TOOLS_OFF.replace(
                "source from VistA, the US Department of Veterans\nAffairs health system", "source"
            ) + "\n{feedback}"
        prompt = template.format(
            routine=task.routine,
            request=task.request,
            baseline=baseline if baseline.endswith("\n") else baseline + "\n",
            feedback=(
                "\nThe verifier already rejected your previous attempt:\n"
                + "\n".join(feedback)
                if feedback
                else ""
            ),
        )
        try:
            proc = subprocess.run(
                self._command(prompt),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                cwd=self._workdir(),
                env=self._environment(),
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentUnavailable(
                f"the Rosetta harness run exceeded {self.timeout_s}s"
            ) from exc
        except OSError as exc:
            raise AgentUnavailable(f"could not run the Rosetta harness: {exc}") from exc

        output = proc.stdout
        self.transcripts.append(output + proc.stderr)
        if proc.returncode != 0:
            raise AgentUnavailable(
                f"the Rosetta harness exited {proc.returncode}: {proc.stderr.strip()[:400]}"
            )

        # JSON events keep terminal banners and tool output out of candidates.
        text_parts = []
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "error":
                raise AgentUnavailable(f"the Rosetta harness reported an error: {str(event.get('error'))[:400]}")
            if event.get("type") == "text":
                text_parts.append(event.get("part", {}).get("text", ""))
        output = "\n".join(text_parts)
        blocks = _FENCE.findall(output)
        if not blocks:
            raise AgentUnavailable(
                "the model produced no fenced code block; cannot extract a candidate"
            )
        candidate = blocks[-1]
        if not candidate.endswith("\n"):
            candidate += "\n"
        tail = _FENCE.sub("", output).strip()
        return Attempt(
            n=len(feedback) + 1,
            candidate_src=candidate,
            explanation=tail[-1500:] or "(no prose returned)",
            diff=unified_diff(baseline, candidate, task.routine),
        )
