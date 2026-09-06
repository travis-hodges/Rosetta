# How someone uses Rosetta

Rosetta is an editor platform for obscure legacy applications and their
databases, with supporting final evaluation, benchmark, model, and training
services. For the hackathon, the platform is optimized for VA VistA MUMPS.

This document is the contract for the *surface*, the way
[`docs/PROJECT.md`](PROJECT.md) §7 is the contract for the *core*. It does not
add capability — everything here was already reachable through eleven separate
`python3 -m rosetta.*` entry points. That was the problem: the modules were
fine, and nobody could tell from the outside which one they wanted.

Status of each surface is marked **live** (works on a configured machine
today), **partial**, or **specified** (designed here, not built). Nothing is
described as working that does not work.

---

## The one-screen model

```
 operational request
        │
        ▼
 DISCOVER ─▶ CONTRACT ─▶ EDIT ─▶ EVALUATE ─▶ SHIP
 code + DB    expected     code    intended +   package +
 impact       state delta  + DB    preserved    rollback
        │                    │          │
        └────────────────────┴──────────┘
                             ▼
                 executable evaluator
                 benchmark + training lab
```

The editor and database change workspace are the product. The verifier provides
ground truth at the final evaluation gate. The benchmark measures whether the
editor and its tools improve the engine. Model registration and training feed
better engines back into the same platform.

The default surface and its supporting entry points are the same underneath:

| | What it is | State |
|---|---|---|
| **TUI** | bare `rosetta`: operational changes, code editing, database work, and evaluation | **live** |
| **Standalone CLI** | `rosetta change`, `db`, `edit`, `verify`, `bench`, model and training commands | **live** |
| **Editor / agent host** | the MCP server, 8 tools, `rosetta mcp serve` | **live** |

None of these is a separate product. The TUI and standalone commands consume
the same implementation in [`rosetta/workflow.py`](../rosetta/workflow.py),
[`rosetta/database.py`](../rosetta/database.py), and `rosetta.core`.

---

## Install

```bash
git clone <this repo> && cd Rosetta
bash scripts/install.sh --bin-dir "$HOME/.local/bin"
cd /path/to/the/project
rosetta                          # opens the TUI here
```

`rosetta` opens the current directory and uses it as the default routine
corpus. `rosetta /another/project` opens another directory. The launcher
injects Rosetta's config in memory, so it does not leave `.opencode` files in
the project.

The first screen says `MUMPS change…` and starts with **Translator 1.0**.
Press Tab to cycle through **Rosetta Agent**, **Rosetta Plan**, and **Rosetta
Verify**—Rosetta itself never turns off. Use `/start` for a short capability
orientation, `/change REQUEST` for an intentional code-and-database change, or
`/pipeline REQUEST` for a focused code workflow. Type `/` for the supporting
services.

For a panel, `/demo` is the compact proof of the thesis. It attempts a fresh,
bounded YottaDB flight on real VA code, animates the actual proof stages in the
TUI, catches a behavior-changing candidate, replays the repaired candidate,
and leaves two content-addressed receipts in `.rosetta/proofs/`. A live failure
falls back immediately after the presentation budget to the committed AJETIU2
audit trace. The fallback says **RECORDED AUDIT TRACE** on screen; it is never
presented as a fresh run.

`rosetta --help` and `rosetta status --plain` work offline with no
container, network or credentials. Everything that needs those is loaded only
by the command that needs it.

`rosetta doctor` is the one command to run when something is wrong. It answers
four questions in order — python, verifier, model access, data — and stops
guessing after the first `--`.

## 1. Change database state — `rosetta change` and `rosetta db`

For application content managed by FileMan, the TUI agent uses the direct
proof-of-concept filer instead of inventing global nodes:

```bash
rosetta fileman --file 4 --field .01 "NEW FACILITY"              # create
rosetta fileman --file 4 --ien 21790 --field .01 "RENAMED"       # update
rosetta fileman --file 4 --ien 21790 --field .01 @               # FileMan delete
```

Values are supplied in external FileMan format. Creation calls `UPDATE^DIE`;
updates call transactional `FILE^DIE`; the command rereads each filed value
before reporting success. This path intentionally omits the plan/approval
ceremony for the local proof of concept.

**live.** This is the primary operational change primitive.

```bash
rosetta db get '^VA(4,123,0)'

rosetta change \
  --request "Update the test facility record" \
  --set '^VA(4,123,0)' 'NEW VALUE'

rosetta db preview .rosetta/changes/chg-....plan.json
rosetta db apply .rosetta/changes/chg-....plan.json --yes
rosetta db rollback .rosetta/changes/chg-....receipt.json --yes
```

The plan captures current values from the active container and instance. It is
content-addressed and cannot be edited silently. Preview rereads every node and
blocks if another operator changed it. Apply captures a full database snapshot,
rechecks the optimistic-lock preconditions, commits all exact-node SET/KILL
operations atomically, rereads the persistent after-state, and writes a receipt
containing the rollback snapshot. Normal rollback is an atomic inverse guarded
by the applied after-values; the full online snapshot is retained for disaster
recovery. KILL refuses nodes with descendants.

This low-level editor does not pretend that every VistA record is safe to create
with raw globals. Top-level FileMan records use `rosetta fileman`, which invokes
the supported DBS APIs so validation and cross-references run. More complex
subfiles and application-specific workflows still require a purpose-built
installation routine. The exact-node workspace remains useful for inspection,
controlled configuration, and fixtures.

## 2. Refactor a routine, evaluated — `rosetta edit`

**live.** A focused workflow for repairs and behavior-preserving refactors.

```bash
rosetta edit ORCRC --request "Handle a null array without erroring" --model opus
```

What happens, in order:

1. the routine is read from `data/routines/`
2. inputs are resolved — a stored suite if one exists, otherwise the validated
   cases carried by the built task set, and it tells you which
3. the model proposes a complete replacement, and the diff is printed
4. **the verifier runs both versions against the real database** and prints its
   own verdict
5. on divergence, the specific node that moved is fed back and the model tries
   again, up to `--attempts`
6. on success the candidate is written to a file — never over your routine

The verdict printed is always the harness's, never the model's claim. This is a
regression evaluation primitive. An intentional feature needs a change
contract describing expected divergence; equivalence to the old routine is not
proof that a new capability works.

The generated source candidate is not silently installed over a production
routine. Database plans, by contrast, can be explicitly applied through the
Change workspace or `rosetta db apply`, with pinned preconditions, typed
confirmation, atomic mutation, a receipt, and an exact inverse rollback.

## 3. Bring your own model — `rosetta model`

**live.**

```bash
rosetta model add opus   anthropic/claude-opus-5 --notes "frontier baseline"
rosetta model add local  ollama/qwen2.5-coder:32b
rosetta model test local           # one live call: reachable and parseable
rosetta model list
```

Rosetta does not host models and holds no credentials — OpenCode owns those,
which keeps secrets out of the repository and out of every trace. Registering
a model means telling Rosetta which id to pass and under what name you want to
see it in a report.

Registered names and raw ids are interchangeable everywhere `--model` is
accepted, so an operator who registered nothing is never blocked.

Two refusals worth knowing, both because a published number depends on them:

- registering a name that already exists needs `--replace`. Two runs reporting
  the same name for different models would make both numbers meaningless.
- `rosetta model test` proves reachability and output format. It does not
  prove competence, and it says so. Competence is measured, not tested.

## 4. Measure the editor — `rosetta bench`

**live** end to end; **no number is published yet.**

```bash
rosetta bench build                      # generate tasks from the eval split
rosetta bench run --model local          # both conditions, N attempts
rosetta bench status                     # progress of a long run
rosetta bench report                     # writes results/summary.json
```

Every task is one working routine with exactly one injected regression, and
every model sees the identical set. Two conditions:

| | The model gets |
|---|---|
| `baseline` | the source, and nothing else |
| `scaffolded` | the source and Rosetta's tools, including `verify_change` |

Tools-off means tools-off: the scaffolded and baseline runs execute in
different working directories, because a flag would leave the tool server one
config reload away from being live.

The scored verdict is the harness's post-hoc verification, never the agent's
assertion. The gap between the two **is** the false-confidence rate.

`rosetta bench report` is the only thing that writes `results/summary.json`,
and the site refuses an invalid report and shows a pending state rather than a
placeholder. There is no way to pass a figure in by hand.

## 5. Improve an engine — `rosetta train`

**partial.** Data generation is live; submission is deliberately absent.

```bash
rosetta train labels     # comprehension pairs from static analysis
rosetta train sft        # chat-format JSONL, split-checked
rosetta train grader     # the verifier as a reward function
rosetta train submit     # explains how, and why we do not do it for you
```

Labels come from static analysis — call graph, global refs, FileMan data
dictionary, routine structure, idiom — so none of them can be wrong in the way
a model-generated label can be wrong. Generation is restricted to the train
side of `data/tasks/split.lock.json`, and `rosetta train sft` re-checks the
split at the last point before data could leave the repository.

`rosetta train submit` does not submit. Sending a training set to a provider
means an account, a spend decision and data leaving the machine, and in the
air-gapped deployment this project targets it cannot leave at all. The command
prints the exact provider invocation and then the two commands that close the
loop:

```bash
rosetta model add tuned provider/ft:your-model-id
rosetta bench run --model tuned
```

That is the whole point of the front door: a fine-tuned model re-enters through
workflow 2 and gets measured by workflow 3, with no new concepts.

`rosetta train grader` wraps the verifier as an RFT reward function. Nothing in
it is MUMPS-specific beyond the runtime it wraps, which is the concrete form of
the corpus-agnostic claim.

## 6. Run final regression evaluation — `rosetta verify`

**live.** No model involved. This is the workflow for a human who made an edit
by hand, and the one that runs in CI.

```bash
rosetta verify ORCRC -c my-rewrite.m
```

```
inputs    12 case(s) from eval_tasks.json
NOT EQUIVALENT — ORCRC candidate diverged on 6 of 12 case(s), with 12
divergence(s) across 2 distinct reference(s).

  [case 0] Written output differs (stdout)
      expected: "FFFFFFFF"
      actual:   ""
  [case 0] Runtime error differs: the candidate raises %YDB-E-LVUNDEF,
           Undefined local variable: ARRAY("") where the baseline succeeds
```

Exit code is `0` for equivalent and `1` for anything else, so this drops into a
pre-commit hook or a pipeline without a wrapper. `--json` gives the full
uncapped report; the text view caps the list so a wall of repeats cannot bury
the first, most actionable divergence.

`--candidate` takes the complete replacement source, not a patch. A patch that
applies cleanly and a routine that compiles are different questions, and only
the second one can be verified.

---

## What is deliberately absent

| Not built | Why |
|---|---|
| `rosetta train submit` actually submitting | data leaving the machine is the operator's decision, and air-gapped it cannot |
| applying a verified candidate to disk | a human decision on a system people depend on |
| a hosted model or hosted code | the air-gap claim is the deployment story |
| a published benchmark number | no full run has happened yet; the TUI reports pending, not a placeholder |

---

## Where the old commands went

Nothing was removed. Every module keeps its own `main` and its own flags, and
`rosetta` passes anything it does not recognise straight through:

```bash
rosetta bench build -- --help          # rosetta.bench.build's own flags
rosetta train sft -- --limit 100
```

| Was | Is |
|---|---|
| `python3 -m rosetta.core.selftest` | `rosetta selftest` |
| `python3 -m rosetta.demo` | `rosetta demo` |
| `python3 -m rosetta.tools` | `rosetta mcp serve` |
| `python3 -m rosetta.bench.split` | `rosetta bench split` |
| `python3 -m rosetta.bench.build` | `rosetta bench build` |
| `python3 -m rosetta.bench.run` | `rosetta bench run` |
| `python3 -m rosetta.bench.report` | `rosetta bench report` |
| `python3 -m rosetta.train.labels` | `rosetta train labels` |
| `python3 -m rosetta.train.sft` | `rosetta train sft` |
| `python3 -m rosetta.train.grader` | `rosetta train grader` |
| — | `rosetta change`, `rosetta db`, `rosetta edit`, `rosetta verify`, `rosetta model` |
