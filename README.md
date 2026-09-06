# Rosetta

**The AI coding environment for code nobody can read.**

Rosetta helps developers understand, change, and prove changes to legacy code.
For the hackathon, it is optimized around real VA VistA MUMPS running under
YottaDB:

> **UNDERSTAND → CHANGE → PROVE**

The U.S. government runs on languages almost nobody can read anymore — MUMPS,
COBOL, JOVIAL, CMS-2 — and AI models are weak on them because there is almost
no training data and no way to export more from a restricted environment. The
dangerous failure mode isn't incapacity, it's that models are **fluent and
confidently wrong** about systems people depend on.

The insight: **you don't need a corpus if you have an interpreter.**

Stop teaching the model the language. Give it a way to check its own work. Snapshot the
system, apply the change, run both versions against the same inputs, and diff the program
output **and** the resulting database state. Correctness stops being an opinion.

The verifier is the proof service inside the coding loop. It also grades the
benchmark, generates verified training data, and acts as the reward function
for reinforcement fine-tuning. The benchmark demonstrates how much better a
model performs with Rosetta; it is not a separate product.

- **Proving ground:** MUMPS / VistA under YottaDB — the only real, public,
  production-scale federal legacy estate.
- **Task:** safe modification, not translation.
- **Headline metric:** false-confidence rate — how often the model asserts correctness
  while verification fails.

📖 **[`docs/PROJECT.md`](docs/PROJECT.md) is the master document** — market context,
architecture, frozen contract, benchmark methodology, build plan, and pitch.

---

## Start here

Install the command once, then open Rosetta from the project you want to work on:

```bash
bash scripts/install.sh --bin-dir "$HOME/.local/bin"
cd /path/to/your/project
rosetta
```

That bare command opens Rosetta's branded, OpenCode-derived TUI in the current
directory. It brings the coding agent, verifier tools, operating instructions
and workflow commands without writing configuration into your project.

| Press Tab | Purpose |
|---|---|
| **Rosetta Agent** | understand, edit, and complete the coding loop |
| **Rosetta Plan** | map routines, calls, globals, and verification cases |
| **Rosetta Verify** | prove and explain behavior without authoring code |

Rosetta is always active; Tab changes its mode. The default demo model appears
as **Translator 1.0**. Type `/start` for orientation, `/pipeline REQUEST` to
run the complete developer loop, or `/` to discover every supporting
capability.

For the judge path, type `/demo`. Rosetta runs a short proof flight against a
real VA VistA routine in YottaDB: a bad candidate is rejected, the repaired
candidate is replayed, database rollback is confirmed, and content-addressed
receipts are saved under `.rosetta/proofs/` in the active project. Animated
stage notifications keep the TUI legible while the interpreter works. If the
live runtime misses the 38-second demo budget, Rosetta fails over to the
committed AJETIU2 audit trace and labels it **RECORDED**—never live.

`rosetta /another/project` opens a different workspace. Use `--model` to
choose a registered name or provider model, and `--corpus` when the MUMPS
sources are not the project directory.

The same workflows remain available as standalone commands:

| | |
|---|---|
| `rosetta edit ROUTINE -m "..."` | change a routine with the verifier in the loop |
| `rosetta verify ROUTINE -c f.m` | check a change you already made (exit 1 on divergence) |
| `rosetta model add NAME id` | bring your own model |
| `rosetta bench run --model NAME` | measure it on the held-out eval set |
| `rosetta train sft` | turn verified work into training data |
| `rosetta gui` | optional browser view, on a loopback port |

📖 **[`docs/WORKFLOWS.md`](docs/WORKFLOWS.md)** walks all five end to end and
shows how the TUI, standalone commands, MCP tools and optional browser view
reach the same verifier.

The underlying `python3 -m rosetta.*` entry points still work and keep their
own flags. `rosetta --help` lists the supported front-door aliases.

---

## What works today

Verified on this machine against a live WorldVistA container. Commands are runnable.

| | Status |
|---|---|
| **The verifier** | `python3 -m rosetta.core.selftest` — passes in ~2.5s |
| **MCP tool server** | 8 tools over stdio; `rosetta mcp list` reports `rosetta connected` |
| **The side-by-side** | `python3 -m rosetta.demo` — runs offline, no container, no network |
| **Live proof flight** | TUI `/demo` — live YottaDB first, bounded recorded fallback, receipts |
| **Mutation generator** | 8 operators; 0.00% defect rate measured against the real YottaDB compiler |
| **Split lock** | Written and frozen: 350 train / 150 eval, partitioned by duplicate cluster |
| **Comprehension labels** | 1,493 pairs from the train split, plus the RFT grader |
| **Benchmark results** | **None published.** No full run has happened yet. |
| **Default UI** | `rosetta` — branded OpenCode TUI, project-aware tools and workflow commands |
| **Browser view** | `rosetta gui` — verify, edit, models and runs, loopback only |

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

## Website

The landing page is [`index.html`](index.html) plus [`src/styles.css`](src/styles.css) and
[`src/main.js`](src/main.js): a single scrolling page with original canvas artwork, served
as plain static files. There is no bundler and no package installation; Node 22+ is needed
only for the optional server, build, and tests:

```bash
npm run dev
npm test
npm run build
npm run preview
```

`npm run build` copies the page, `src/`, and `public/` into `dist/`, which is what Vercel
publishes. It parses `src/main.js` and checks every local asset the page references, so a
syntax error or a dead path fails the build rather than the first visitor's browser.

Typography loads Space Grotesk and Space Mono from the Google Fonts CDN; everything else is
local. The benchmark contract is enforced by the producer in
[`rosetta/bench/report.py`](rosetta/bench/report.py) — the page states that published
results are pending and never hand-authors performance values.
