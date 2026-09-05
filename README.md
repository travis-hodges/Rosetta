# Rosetta

Rosetta is a GitHub Issues to local Codex orchestration system. Create a task on
the repository's Issues page and a signed-in Codex agent on this Mac claims it,
works in an isolated Git worktree, coordinates through issue and pull-request
history, and returns a reviewable pull request.

## Assign an agent

Open **Issues → New issue**, then choose:

- **Builder agent task** for implementation.
- **Research agent task** for investigation or architecture.
- **Review agent task** for independent review.

Submitting the form applies `agent:ready`, which is the assignment signal. The
role and lifecycle labels show who owns the task and what state it is in.

## Local setup

Requirements: macOS, Git, GitHub CLI authenticated with repository access, and
Codex CLI authenticated with your Codex account.

```bash
python3 orchestration/orchestrator.py doctor
python3 orchestration/orchestrator.py setup-github
python3 orchestration/orchestrator.py install
```

The current installation is intentionally temporary. A one-shot LaunchAgent is
scheduled to permanently remove the background service and all of its runtime
state three days after installation; it does not delete this repository.

See [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md) for the lifecycle, security
boundary, configuration, and operations.

## Landing page

The repository includes Rosetta's Vite-powered product landing page. To run it
locally:

```bash
npm install
npm run dev
```

Create the production bundle with `npm run build`.
