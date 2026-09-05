#!/usr/bin/env python3
"""Turn owner-authored GitHub Issues into isolated local Codex tasks."""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import fcntl
import json
import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterable


LABELS = {
    "agent:ready": ("1d76db", "Queued for the local Codex orchestrator"),
    "agent:running": ("fbca04", "Claimed by a local Codex task"),
    "agent:done": ("0e8a16", "Agent completed and reported its work"),
    "agent:failed": ("d93f0b", "Agent or orchestration failed"),
    "agent:blocked": ("b60205", "Not authorized or needs human intervention"),
    "agent:builder": ("5319e7", "Implementation agent role"),
    "agent:researcher": ("0969da", "Research and architecture agent role"),
    "agent:reviewer": ("8b5cf6", "Independent review agent role"),
}
ROLE_LABELS = ("agent:builder", "agent:researcher", "agent:reviewer")
SERVICE_LABEL = "com.rosetta.codex-orchestrator"


class CommandError(RuntimeError):
    """Raised when a required subprocess command fails."""


@dataclasses.dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    author: str
    labels: tuple[str, ...]
    url: str
    comments: tuple[dict[str, Any], ...] = ()

    @property
    def role(self) -> str:
        return next((label.removeprefix("agent:") for label in self.labels if label in ROLE_LABELS), "builder")


def run(
    args: Iterable[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [str(arg) for arg in args]
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=capture,
        text=True,
        input=input_text,
    )
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "no output").strip()
        raise CommandError(f"{' '.join(command)} failed ({completed.returncode}): {detail}")
    return completed


def slugify(value: str, limit: int = 42) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "task")[:limit].rstrip("-")


def truncate(value: str, limit: int = 55_000) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 80] + "\n\n… output truncated by the Rosetta orchestrator."


class GitHub:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _json(self, args: list[str]) -> Any:
        output = run(["gh", *args], cwd=self.root).stdout
        return json.loads(output)

    def repo(self) -> dict[str, Any]:
        return self._json(["repo", "view", "--json", "nameWithOwner,url,owner,defaultBranchRef"])

    def ready_issues(self) -> list[Issue]:
        rows = self._json(
            [
                "issue",
                "list",
                "--state",
                "open",
                "--label",
                "agent:ready",
                "--limit",
                "100",
                "--json",
                "number,title,body,author,labels,url",
            ]
        )
        return [self._issue_from_row(row) for row in rows]

    def issue(self, number: int) -> Issue:
        row = self._json(
            [
                "issue",
                "view",
                str(number),
                "--json",
                "number,title,body,author,labels,url,comments",
            ]
        )
        return self._issue_from_row(row)

    @staticmethod
    def _issue_from_row(row: dict[str, Any]) -> Issue:
        return Issue(
            number=int(row["number"]),
            title=row.get("title") or "Untitled task",
            body=row.get("body") or "",
            author=(row.get("author") or {}).get("login") or "",
            labels=tuple(item["name"] for item in row.get("labels", [])),
            url=row.get("url") or "",
            comments=tuple(row.get("comments") or ()),
        )

    def recent_context(self) -> str:
        rows = self._json(
            [
                "issue",
                "list",
                "--state",
                "all",
                "--limit",
                "20",
                "--search",
                "label:agent:done,agent:running,agent:failed",
                "--json",
                "number,title,url,state,updatedAt,labels",
            ]
        )
        if not rows:
            return "No earlier orchestrated issues are available."
        lines = []
        for row in rows:
            labels = ", ".join(item["name"] for item in row.get("labels", []))
            lines.append(f"- #{row['number']} [{row['state']}] {row['title']} ({labels}) {row['url']}")
        return "\n".join(lines)

    def edit_labels(self, number: int, *, add: Iterable[str] = (), remove: Iterable[str] = ()) -> None:
        args = ["issue", "edit", str(number)]
        for label in remove:
            args.extend(["--remove-label", label])
        for label in add:
            args.extend(["--add-label", label])
        run(["gh", *args], cwd=self.root)

    def comment(self, number: int, body: str) -> None:
        run(["gh", "issue", "comment", str(number), "--body", truncate(body)], cwd=self.root)

    def pull_request(self, *, number: int, branch: str, title: str, final_message: str) -> str:
        body = (
            f"Closes #{number}\n\n"
            "Created by the Rosetta local Codex orchestrator.\n\n"
            "## Agent report\n\n"
            f"{truncate(final_message, 40_000)}"
        )
        completed = run(
            [
                "gh",
                "pr",
                "create",
                "--base",
                "main",
                "--head",
                branch,
                "--title",
                title,
                "--body",
                body,
            ],
            cwd=self.root,
        )
        return completed.stdout.strip()


class Orchestrator:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.github = GitHub(self.root)
        self.state = self.root / ".git" / "rosetta-orchestrator"
        self.worktrees = self.state / "worktrees"
        self.logs = self.state / "logs"
        self.poll_seconds = max(5, int(os.getenv("ROSETTA_POLL_SECONDS", "30")))
        self.max_workers = max(1, int(os.getenv("ROSETTA_MAX_WORKERS", "2")))
        self.sandbox = os.getenv("ROSETTA_SANDBOX", "workspace-write")
        if self.sandbox not in {"workspace-write", "danger-full-access"}:
            raise ValueError("ROSETTA_SANDBOX must be workspace-write or danger-full-access")
        self.state.mkdir(parents=True, exist_ok=True)
        self.worktrees.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)

    def owner(self) -> str:
        configured = os.getenv("ROSETTA_ALLOWED_ACTOR", "").strip()
        if configured:
            return configured
        return self.github.repo()["owner"]["login"]

    def doctor(self) -> None:
        problems: list[str] = []
        for executable in ("git", "gh", "codex"):
            if not shutil.which(executable):
                problems.append(f"Missing executable: {executable}")
        if run(["git", "rev-parse", "--is-inside-work-tree"], cwd=self.root, check=False).returncode != 0:
            problems.append("Rosetta is not a Git repository")
        if run(["gh", "auth", "status"], cwd=self.root, check=False).returncode != 0:
            problems.append("GitHub CLI is not authenticated")
        if run(["codex", "login", "status"], cwd=self.root, check=False).returncode != 0:
            problems.append("Codex CLI is not authenticated")
        repo_check = run(["gh", "repo", "view", "--json", "nameWithOwner"], cwd=self.root, check=False)
        if repo_check.returncode != 0:
            problems.append("No accessible GitHub remote repository is configured")
        if problems:
            raise RuntimeError("Doctor found problems:\n- " + "\n- ".join(problems))
        repo = self.github.repo()
        print(f"Ready: {repo['nameWithOwner']} ({repo['url']}); owner allowlist: {self.owner()}")

    def setup_github(self) -> None:
        run(["gh", "repo", "edit", "--enable-issues"], cwd=self.root)
        for name, (color, description) in LABELS.items():
            run(
                [
                    "gh",
                    "label",
                    "create",
                    name,
                    "--color",
                    color,
                    "--description",
                    description,
                    "--force",
                ],
                cwd=self.root,
            )
        print(f"Configured {len(LABELS)} orchestration labels.")

    def build_prompt(self, issue: Issue) -> str:
        comments = []
        for item in issue.comments:
            author = (item.get("author") or {}).get("login") or "unknown"
            body = item.get("body") or ""
            comments.append(f"### Comment by {author}\n{body}")
        comment_text = "\n\n".join(comments) if comments else "No comments."
        return f"""You are the primary local Codex agent assigned to GitHub issue #{issue.number}.

Role selected on GitHub: {issue.role}
Issue URL: {issue.url}
Issue author (validated by the runner): {issue.author}

## Title
{issue.title}

## Issue body
{issue.body}

## Issue comments
{comment_text}

## Recent shared orchestration context
{self.github.recent_context()}

## Required operating behavior
- Work only in this isolated Git worktree and follow AGENTS.md.
- Inspect the actual repository before editing.
- Complete the full task and its acceptance criteria; do not stop at a plan.
- For a non-atomic task, spawn at least one appropriate project subagent for a bounded independent investigation, implementation slice, or verification step. Wait for it and integrate its evidence.
- Run verification proportional to the change and fix failures caused by your work.
- Do not commit, push, open a PR, edit labels, or post GitHub comments. The runner handles lifecycle operations after you finish.
- End with sections named Summary, Verification, Shared knowledge, and Remaining risk.
"""

    def claim(self, issue: Issue) -> bool:
        allowed = self.owner()
        if issue.author != allowed:
            self.github.edit_labels(
                issue.number,
                add=("agent:blocked",),
                remove=("agent:ready", "agent:running"),
            )
            self.github.comment(
                issue.number,
                f"⛔ Rosetta did not execute this issue because `{issue.author}` is not the allowed actor `{allowed}`.",
            )
            return False
        self.github.edit_labels(
            issue.number,
            add=("agent:running",),
            remove=("agent:ready", "agent:failed", "agent:blocked"),
        )
        self.github.comment(
            issue.number,
            f"🤖 Claimed by the local Rosetta Codex orchestrator as a **{issue.role}** task. A dedicated worktree and Codex task are starting.",
        )
        return True

    def _branch_and_worktree(self, issue: Issue) -> tuple[str, Path]:
        branch = f"codex/issue-{issue.number}-{slugify(issue.title)}"
        worktree = self.worktrees / f"issue-{issue.number}"
        if worktree.exists():
            if (worktree / ".git").exists():
                return branch, worktree
            raise RuntimeError(f"Refusing to overwrite unexpected path: {worktree}")

        run(["git", "fetch", "--prune", "origin", "main"], cwd=self.root)
        remote = run(["git", "ls-remote", "--exit-code", "--heads", "origin", branch], cwd=self.root, check=False)
        local = run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=self.root, check=False)
        if remote.returncode == 0:
            run(["git", "worktree", "add", "-B", branch, str(worktree), f"origin/{branch}"], cwd=self.root)
        elif local.returncode == 0:
            run(["git", "worktree", "add", str(worktree), branch], cwd=self.root)
        else:
            run(["git", "worktree", "add", "-b", branch, str(worktree), "origin/main"], cwd=self.root)
        return branch, worktree

    def _run_codex(self, issue: Issue, worktree: Path) -> tuple[int, str, str | None]:
        issue_log = self.logs / f"issue-{issue.number}"
        issue_log.mkdir(parents=True, exist_ok=True)
        events_path = issue_log / "events.jsonl"
        stderr_path = issue_log / "stderr.log"
        final_path = issue_log / "final.md"
        prompt_path = issue_log / "prompt.md"
        prompt = self.build_prompt(issue)
        prompt_path.write_text(prompt, encoding="utf-8")

        command = [
            "codex",
            "exec",
            "-C",
            str(worktree),
            "--sandbox",
            self.sandbox,
            "--json",
            "--output-last-message",
            str(final_path),
            "-",
        ]
        with events_path.open("w", encoding="utf-8") as events, stderr_path.open("w", encoding="utf-8") as errors:
            completed = subprocess.run(
                command,
                cwd=worktree,
                input=prompt,
                text=True,
                stdout=events,
                stderr=errors,
                check=False,
            )

        final_message = final_path.read_text(encoding="utf-8") if final_path.exists() else ""
        thread_id: str | None = None
        if events_path.exists():
            for line in events_path.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "thread.started":
                    thread_id = event.get("thread_id")
                    break
        return completed.returncode, final_message.strip(), thread_id

    def process(self, queued: Issue) -> None:
        issue = self.github.issue(queued.number)
        branch = ""
        worktree: Path | None = None
        try:
            branch, worktree = self._branch_and_worktree(issue)
            code, final_message, thread_id = self._run_codex(issue, worktree)
            thread_note = f" Local Codex task: `{thread_id}`." if thread_id else ""
            if code != 0:
                raise RuntimeError(f"Codex exited with status {code}.{thread_note}")

            status = run(["git", "status", "--porcelain"], cwd=worktree).stdout.strip()
            pr_url = ""
            if status:
                run(["git", "add", "-A"], cwd=worktree)
                run(["git", "commit", "-m", f"Resolve #{issue.number}: {issue.title}"], cwd=worktree)
                run(["git", "push", "--set-upstream", "origin", branch], cwd=worktree)
                pr_url = self.github.pull_request(
                    number=issue.number,
                    branch=branch,
                    title=issue.title,
                    final_message=final_message or "Agent completed without a final report.",
                )

            self.github.edit_labels(
                issue.number,
                add=("agent:done",),
                remove=("agent:running", "agent:ready", "agent:failed", "agent:blocked"),
            )
            outcome = f"✅ Local Codex task completed.{thread_note}"
            if pr_url:
                outcome += f"\n\nPull request: {pr_url}"
            else:
                outcome += "\n\nNo repository changes were produced, so no pull request was opened."
            if final_message:
                outcome += f"\n\n## Agent report\n\n{final_message}"
            self.github.comment(issue.number, outcome)

            if worktree and not run(["git", "status", "--porcelain"], cwd=worktree).stdout.strip():
                run(["git", "worktree", "remove", str(worktree)], cwd=self.root, check=False)
        except Exception as exc:
            self.github.edit_labels(
                issue.number,
                add=("agent:failed",),
                remove=("agent:running", "agent:ready"),
            )
            self.github.comment(
                issue.number,
                "❌ Rosetta orchestration failed. The worktree and logs were retained for diagnosis.\n\n"
                f"```text\n{truncate(str(exc), 8_000)}\n```",
            )
            raise

    def poll_once(self) -> None:
        issues = self.github.ready_issues()[: self.max_workers]
        claimed = [issue for issue in issues if self.claim(issue)]
        if not claimed:
            print("No ready issues.")
            return
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self.process, issue): issue for issue in claimed}
            for future, issue in futures.items():
                try:
                    future.result()
                    print(f"Completed issue #{issue.number}")
                except Exception as exc:
                    print(f"Failed issue #{issue.number}: {exc}", file=sys.stderr)

    def serve(self) -> None:
        lock_path = self.state / "service.lock"
        lock_file = lock_path.open("w", encoding="utf-8")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another Rosetta orchestrator is already running") from exc

        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        print(f"Rosetta orchestrator started; poll={self.poll_seconds}s workers={self.max_workers}", flush=True)

        active: dict[concurrent.futures.Future[None], Issue] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while not stop.is_set():
                for future, issue in list(active.items()):
                    if not future.done():
                        continue
                    try:
                        future.result()
                        print(f"Completed issue #{issue.number}", flush=True)
                    except Exception as exc:
                        print(f"Failed issue #{issue.number}: {exc}", file=sys.stderr, flush=True)
                    del active[future]

                available = self.max_workers - len(active)
                if available:
                    try:
                        for issue in self.github.ready_issues()[:available]:
                            if self.claim(issue):
                                active[pool.submit(self.process, issue)] = issue
                    except Exception as exc:
                        print(f"Polling failed: {exc}", file=sys.stderr, flush=True)
                stop.wait(self.poll_seconds)

    def install(self) -> Path:
        self.doctor()
        launch_agents = Path.home() / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True, exist_ok=True)
        plist_path = launch_agents / f"{SERVICE_LABEL}.plist"
        path_parts = sorted({str(Path(exe).parent) for name in ("git", "gh", "codex", "python3") if (exe := shutil.which(name))})
        inherited_path = os.getenv("PATH", "")
        service_path = ":".join(path_parts + ([inherited_path] if inherited_path else []))
        payload = {
            "Label": SERVICE_LABEL,
            "ProgramArguments": [sys.executable, str(Path(__file__).resolve()), "serve"],
            "WorkingDirectory": str(self.root),
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 10,
            "EnvironmentVariables": {
                "PATH": service_path,
                "ROSETTA_POLL_SECONDS": str(self.poll_seconds),
                "ROSETTA_MAX_WORKERS": str(self.max_workers),
                "ROSETTA_SANDBOX": self.sandbox,
                "ROSETTA_ALLOWED_ACTOR": self.owner(),
            },
            "StandardOutPath": str(self.state / "service.stdout.log"),
            "StandardErrorPath": str(self.state / "service.stderr.log"),
        }
        with plist_path.open("wb") as stream:
            plistlib.dump(payload, stream)

        domain = f"gui/{os.getuid()}"
        run(["launchctl", "bootout", domain, str(plist_path)], check=False)
        run(["launchctl", "bootstrap", domain, str(plist_path)])
        run(["launchctl", "enable", f"{domain}/{SERVICE_LABEL}"])
        run(["launchctl", "kickstart", "-k", f"{domain}/{SERVICE_LABEL}"])
        print(f"Installed and started {SERVICE_LABEL}: {plist_path}")
        return plist_path

    def status(self) -> None:
        domain = f"gui/{os.getuid()}/{SERVICE_LABEL}"
        completed = run(["launchctl", "print", domain], check=False)
        if completed.returncode == 0:
            lines = [line.strip() for line in completed.stdout.splitlines() if "state =" in line or "pid =" in line]
            print(f"LaunchAgent loaded ({', '.join(lines) or 'status available'}).")
        else:
            print("LaunchAgent is not loaded.")
        print(f"State directory: {self.state}")


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("doctor", "setup-github", "poll-once", "serve", "install", "status"),
    )
    args = parser.parse_args(argv)
    orchestrator = Orchestrator(repository_root())
    try:
        if args.command == "doctor":
            orchestrator.doctor()
        elif args.command == "setup-github":
            orchestrator.setup_github()
        elif args.command == "poll-once":
            orchestrator.doctor()
            orchestrator.poll_once()
        elif args.command == "serve":
            orchestrator.doctor()
            orchestrator.serve()
        elif args.command == "install":
            orchestrator.install()
        elif args.command == "status":
            orchestrator.status()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
