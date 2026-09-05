# Rosetta

**Ground truth for code nobody can read.**

Rosetta makes AI modification of legacy code *verifiable*. The U.S. government runs on
languages almost nobody can read anymore — MUMPS, COBOL, JOVIAL, CMS-2 — and AI models are
weak on them because there is almost no training data and no way to export more from a
restricted environment. The dangerous failure mode isn't incapacity, it's that models are
**fluent and confidently wrong** about systems people depend on.

The insight: **you don't need a corpus if you have an interpreter.**

Stop teaching the model the language. Give it a way to check its own work. Snapshot the
system, apply the change, run both versions against the same inputs, and diff the program
output **and** the resulting database state. Correctness stops being an opinion.

One component — the verifier — does four jobs: it grades the benchmark, serves as a tool
the agent calls while working, generates verified training data, and acts as the reward
function for reinforcement fine-tuning. That makes the method corpus-agnostic, and it
means Rosetta **never needs to see the customer's code** — which is what makes it
deployable air-gapped.

- **Proving ground:** MUMPS / VistA under YottaDB — the only real, public,
  production-scale federal legacy estate.
- **Task:** safe modification, not translation.
- **Headline metric:** false-confidence rate — how often the model asserts correctness
  while verification fails.

📖 **[`docs/PROJECT.md`](docs/PROJECT.md) is the master document** — market context,
architecture, frozen contract, benchmark methodology, build plan, and pitch.

---

## Not to be confused with: the orchestrator

This repository also contains a **separate build service** in [`orchestration/`](orchestration/):
a local runner that turns owner-authored GitHub issues into isolated agent runs and
reviewable pull requests.

**That service is infrastructure, not the project.** It exists to help build Rosetta. It is
temporary and self-destructs on schedule. When this repository says "Rosetta," it means the
verification system described above — not the orchestrator.

See [`docs/ORCHESTRATION.md`](docs/ORCHESTRATION.md) for how to run it.

---

## Working in this repository

Agents and contributors read [`AGENTS.md`](AGENTS.md) first, then
[`docs/PROJECT.md`](docs/PROJECT.md). Claude sessions additionally read
[`CLAUDE.md`](CLAUDE.md).

The hard rules that protect every published number:

- `rosetta/core/interface.py` is **frozen** — announce before changing it.
- Only `rosetta/core/` touches YottaDB.
- Always `clean_state()` around execution.
- Never rewrite `data/tasks/split.lock.json`.
- Don't re-propose an approach already rejected in `docs/PROJECT.md` §5.

## Running the orchestrator

Requirements: macOS, Git, GitHub CLI authenticated with repository access, and Codex CLI
authenticated with your Codex account.

```bash
python3 orchestration/orchestrator.py doctor
python3 orchestration/orchestrator.py setup-github
python3 orchestration/orchestrator.py install
```

Assign work through **Issues → New issue**, choosing a Builder, Research, or Review agent
task. Submitting the form applies `agent:ready`, which is the assignment signal.

The installation is intentionally temporary. A one-shot LaunchAgent permanently removes the
background service and all of its runtime state three days after installation; it does not
delete this repository.

## Landing page

A Vite-powered landing page is in flight in [PR #4](https://github.com/travis-hodges/Rosetta/pull/4)
and is being built against the specification in `docs/PROJECT.md` §10.

```bash
npm install
npm run dev
```

Create the production bundle with `npm run build`.
