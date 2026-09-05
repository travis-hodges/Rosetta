# Rosetta orchestration

Rosetta turns GitHub Issues into isolated local Codex tasks. GitHub is the
coordination surface; the actual agents run on the Mac where Codex is signed in.

## Lifecycle

1. Create an issue with one of the Builder, Research, or Review templates.
2. The template applies `agent:ready` plus a role label.
3. The local service verifies that the issue author is the repository owner,
   claims the issue, and changes its status to `agent:running`.
4. The service creates a dedicated `codex/issue-...` branch and Git worktree.
5. A local Codex parent task works the issue. `AGENTS.md` tells it to delegate
   independent work to the repository's explorer, implementer, and verifier
   subagents when the task is not atomic.
6. The service commits and pushes any changes, opens a pull request that closes
   the issue when merged, and posts the agent's report back to the issue.
7. The issue receives `agent:done` or `agent:failed`. Later agents receive a
   compact digest of recent agent issues, while issue comments and pull requests
   retain the full durable history.

This is polling rather than an inbound webhook because GitHub cannot directly
reach a laptop behind a firewall. The default interval is 30 seconds.

## Commands

```bash
python3 orchestration/orchestrator.py doctor
python3 orchestration/orchestrator.py setup-github
python3 orchestration/orchestrator.py poll-once
python3 orchestration/orchestrator.py install
python3 orchestration/orchestrator.py schedule-expiry --days 3
python3 orchestration/orchestrator.py status
```

`install` creates and starts a per-user macOS LaunchAgent. Logs, worktrees, and
per-issue Codex output live under `.git/rosetta-orchestrator/` and are never
committed.

`schedule-expiry` installs a separate one-shot LaunchAgent. At the deadline it
removes the main service definition, terminates active issue agents, unloads the
service, removes registered orchestration worktrees, recursively deletes all
runtime state, and finally deletes its own installed helper and LaunchAgent.
The Git repository and GitHub repository remain intact as an audit trail. The
calendar trigger survives reboot and executes after the next login/wake if the
Mac is unavailable at the exact deadline.

## Configuration

The service accepts these environment variables when run manually:

| Variable | Default | Purpose |
| --- | --- | --- |
| `ROSETTA_POLL_SECONDS` | `30` | Seconds between GitHub polls |
| `ROSETTA_MAX_WORKERS` | `2` | Maximum issue agents running concurrently |
| `ROSETTA_SANDBOX` | `workspace-write` | Codex sandbox (`workspace-write` or `danger-full-access`) |
| `ROSETTA_ALLOWED_ACTOR` | repository owner | Only this GitHub login may author runnable issues |

The installed LaunchAgent uses the defaults captured by the installer. Re-run
`install` after intentionally changing its environment.

## Security boundary

- The repository is private by default and only owner-authored issues run.
- The agent starts with `workspace-write`, not unrestricted filesystem access.
- Codex does not receive responsibility for commits, pushes, labels, comments,
  or pull requests; the deterministic runner performs those operations.
- No GitHub or OpenAI tokens are stored in this repository. The service reuses
  existing `gh` and local Codex authentication.
- Automated changes always land through a branch and pull request for review.

Do not make the repository public without revisiting the author allowlist and
the risk of executing untrusted issue content or repository code.
