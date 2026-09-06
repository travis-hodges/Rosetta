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

## Start here

One command, five workflows. Every command prints the next one.

```bash
export PATH="$PWD/bin:$PATH"
rosetta            # the map: what is wired on this machine, and what is not
rosetta doctor     # can this machine actually run the verifier
```

| | |
|---|---|
| `rosetta edit ROUTINE -m "..."` | change a routine with the verifier in the loop |
| `rosetta verify ROUTINE -c f.m` | check a change you already made (exit 1 on divergence) |
| `rosetta model add NAME id` | bring your own model |
| `rosetta bench run --model NAME` | measure it on the held-out eval set |
| `rosetta train sft` | turn verified work into training data |
| `rosetta gui` | all five of those in a browser, on a loopback port |

📖 **[`docs/WORKFLOWS.md`](docs/WORKFLOWS.md)** walks all five end to end, in
both the terminal and the GUI, and marks exactly what is live and what is
specified but unbuilt.

The old `python3 -m rosetta.*` entry points all still work and keep their own
flags; `rosetta` passes anything it does not recognise straight through.

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

---

## What works today

Verified on this machine against a live WorldVistA container. Commands are runnable.

| | Status |
|---|---|
| **The verifier** | `python3 -m rosetta.core.selftest` — passes in ~2.5s |
| **MCP tool server** | 8 tools over stdio; `opencode mcp list` reports `rosetta connected` |
| **The side-by-side** | `python3 -m rosetta.demo` — runs offline, no container, no network |
| **Mutation generator** | 8 operators; 0.00% defect rate measured against the real YottaDB compiler |
| **Split lock** | Written and frozen: 350 train / 150 eval, partitioned by duplicate cluster |
| **Comprehension labels** | 1,493 pairs from the train split, plus the RFT grader |
| **Benchmark results** | **None published.** No full run has happened yet. |
| **The GUI** | `rosetta gui` — verify, edit, models and runs, loopback only |

The load-bearing demonstration is in the selftest: a `CMP_FLIP` injected into a real VistA
routine is caught by diffing global state, and the report names the exact node that moved —
`^PXRMINDX(9000010.71,"IP","10D","Z00.00",777,3250101,4242)`. That is the whole thesis in
one line of output.

`python3 -m rosetta.demo` shows the money moment on `^DPT(DFN,.21)`, the next-of-kin node of
the PATIENT file. 765 patients have that node; 69 have it present with the name piece blank.
A plausible-looking refactor returns an empty string for every one of those where the
original returned `"Not Entered"` — fluently explained, entirely wrong, and caught
mechanically.

**No benchmark number is published, and the site shows a pending state rather than a
placeholder.** Every figure it can display is read from `results/summary.json`, which is
written only by a real run.

## Working in this repository

Agents and contributors read [`AGENTS.md`](AGENTS.md) first, then
[`docs/PROJECT.md`](docs/PROJECT.md) for the architecture and
[`docs/WORKFLOWS.md`](docs/WORKFLOWS.md) for the surface. Claude sessions additionally read
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

The polling service is currently **disabled** (`launchctl disable`). It had been running from
a second, stale checkout and opening pull requests from an eleven-commit-old base. Re-enable
with `launchctl enable` only after confirming which checkout its plist points at.

## Website

The product website lives in [`web/index.html`](web/index.html): one self-contained,
offline-capable HTML file with inline CSS, JavaScript, and original vector/canvas artwork.
Open it directly in a browser, or serve it locally with Node 22+:

```bash
npm run dev
npm test
npm run build
npm run preview
```

No package installation is needed. The optional build copies the site to `dist/` for
Vercel. Views use hash routes so they work offline.

See [`web/README.md`](web/README.md) for release links, account integration, the benchmark
report contract, and verification limits. Login and installers stay explicitly unavailable
until real services and release artifacts are configured.
