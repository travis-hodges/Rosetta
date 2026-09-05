#!/usr/bin/env python3
"""One-shot, self-removing shutdown helper for the Rosetta LaunchAgent."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path


SERVICE_LABEL = "com.rosetta.codex-orchestrator"
EXPIRY_LABEL = f"{SERVICE_LABEL}.expiry"


def validate_paths(
    *,
    repository_root: Path,
    state_dir: Path,
    service_plist: Path,
    expiry_plist: Path,
    support_dir: Path,
) -> None:
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    expected_support = Path.home() / "Library" / "Application Support" / "RosettaOrchestrator"
    expected = {
        "state directory": repository_root / ".git" / "rosetta-orchestrator",
        "service plist": launch_agents / f"{SERVICE_LABEL}.plist",
        "expiry plist": launch_agents / f"{EXPIRY_LABEL}.plist",
        "support directory": expected_support,
    }
    actual = {
        "state directory": state_dir,
        "service plist": service_plist,
        "expiry plist": expiry_plist,
        "support directory": support_dir,
    }
    mismatches = [name for name in expected if actual[name] != expected[name]]
    if mismatches:
        details = ", ".join(f"{name}: {actual[name]} != {expected[name]}" for name in mismatches)
        raise RuntimeError(f"Refusing unsafe cleanup targets: {details}")


def terminate_codex_processes(state_dir: Path) -> None:
    process_groups: set[int] = set()
    for pid_path in state_dir.glob("logs/issue-*/codex.pid"):
        try:
            process_group = int(pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        command = subprocess.run(
            ["ps", "-p", str(process_group), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
        ).stdout
        if "codex" in command.lower():
            process_groups.add(process_group)
    for process_group in process_groups:
        try:
            os.killpg(process_group, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if process_groups:
        time.sleep(2)
    for process_group in process_groups:
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass


def remove_registered_worktrees(repository_root: Path, state_dir: Path) -> None:
    worktree_root = state_dir / "worktrees"
    if not worktree_root.exists():
        return
    for candidate in worktree_root.iterdir():
        if candidate.is_dir():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(candidate)],
                cwd=repository_root,
                check=False,
                capture_output=True,
                text=True,
            )
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )


def expire(
    *,
    not_before: float,
    repository_root: Path,
    state_dir: Path,
    service_plist: Path,
    expiry_plist: Path,
    support_dir: Path,
) -> None:
    validate_paths(
        repository_root=repository_root,
        state_dir=state_dir,
        service_plist=service_plist,
        expiry_plist=expiry_plist,
        support_dir=support_dir,
    )
    delay = not_before - time.time()
    if delay > 0:
        time.sleep(delay)

    # Remove both definitions first so launchd cannot revive either job.
    service_plist.unlink(missing_ok=True)
    expiry_plist.unlink(missing_ok=True)
    terminate_codex_processes(state_dir)
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}/{SERVICE_LABEL}"],
        check=False,
        capture_output=True,
        text=True,
    )
    remove_registered_worktrees(repository_root, state_dir)
    if state_dir.exists():
        shutil.rmtree(state_dir)
    if support_dir.exists():
        shutil.rmtree(support_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--not-before", type=float, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--service-plist", type=Path, required=True)
    parser.add_argument("--expiry-plist", type=Path, required=True)
    parser.add_argument("--support-dir", type=Path, required=True)
    args = parser.parse_args()
    expire(
        not_before=args.not_before,
        repository_root=args.repository_root,
        state_dir=args.state_dir,
        service_plist=args.service_plist,
        expiry_plist=args.expiry_plist,
        support_dir=args.support_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
