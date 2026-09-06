# How someone uses Rosetta

Five workflows, available from one project-aware TUI and from standalone
commands. Every standalone command prints the next one.

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
                     ┌─────────────────────────────────────┐
   your change ─────▶│  1  edit      change it, verified   │
   your file   ─────▶│  5  verify    check what you wrote  │
                     ├─────────────────────────────────────┤
   your model  ─────▶│  2  model     bring your own        │
                     │  3  bench     measure it            │
                     │  4  train     improve it            │
                     └──────────────┬──────────────────────┘
                                    │  every one of them calls
                                    ▼
                          the verifier — the only
                          thing that decides truth
```

The shape matters more than the commands. There is exactly one arbiter, and
all five workflows are the same loop viewed from different distances: run both
versions, diff output *and* database state, believe the diff.

The default surface and its supporting entry points are the same underneath:

| | What it is | State |
|---|---|---|
| **TUI** | bare `rosetta`: branded OpenCode with Rosetta's agent, tools and command palette | **live** |
| **Standalone CLI** | `rosetta edit`, `verify`, `model`, `bench` and `train` | **live** |
| **Editor / agent host** | the MCP server, 8 tools, `rosetta mcp serve` | **live** |
| **Browser view** | `rosetta gui`, the same five workflows in a browser | **live** |

None of these is a separate implementation. `rosetta edit`, the Edit view and
an agent in OpenCode all consume the same generators in
[`rosetta/workflow.py`](../rosetta/workflow.py) and call the identical
`verify_change`, so a verdict reached in a browser, in an editor, and on the
benchmark is the same verdict. The front ends own presentation and nothing
else — the GUI computes exactly one number of its own, an elapsed-time clock.

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
the project. Type `/` in the TUI to discover the product workflows.

`rosetta --help` and `rosetta status --plain` work offline with no
container, network or credentials. Everything that needs those is loaded only
by the command that needs it.

`rosetta doctor` is the one command to run when something is wrong. It answers
four questions in order — python, verifier, model access, data — and stops
guessing after the first `--`.

For the browser surface:

```bash
rosetta gui                      # http://127.0.0.1:7391
rosetta gui --port 8080 --no-open
```

The server binds a loopback interface and refuses anything else. That is not a
setting: Rosetta is built to run inside an enclave, and a verification tool
listening on a routable interface there is a finding, not a feature. It serves
three hand-written files with no remote asset of any kind, so the page opens on
a machine that has never seen a package index.

---

## 1. Change a routine, verified — `rosetta edit`

**live.** The workflow the product exists for.

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

The verdict printed is always the harness's, never the model's claim. That
separation is the product: a model asserting "this is behaviour preserving"
while the verifier disagrees is the exact failure this catches, and it is what
the headline metric counts.

Nothing is applied for you. Applying a change to a system people depend on is
a human decision, and a tool that made it silently would be the wrong tool.

## 2. Bring your own model — `rosetta model`

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

## 3. Measure it — `rosetta bench`

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

## 4. Improve it — `rosetta train`

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

## 5. Check what you wrote — `rosetta verify`

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

## The GUI

**live.** `rosetta gui` serves four views over the same workflows. Nothing in
the browser can compute a number the CLI cannot reproduce: the front end
renders what the API returned, and anything statistical is read from
`results/summary.json`, which only `rosetta bench report` writes.

### Verify — the whole thesis on one screen

Pick a routine and the page immediately reports whether it *can* be verified
(`134 lines · 12 case(s) from eval_tasks.json`). Finding out that a routine has
no inputs after writing a candidate is the wrong time to find out.

Paste a rewrite, or drop a `.m` file — a dropped `ORCRC.m` fills in the routine
name too, because that is what dropping it meant. The verdict banner is the
verifier's own text, and each divergence expands to the case index, the global
reference, the `^`-delimited piece, the FileMan field that piece is, and the
before and after values.

### Edit — the repair loop, made visible

One card per attempt: the model's diff, then the verifier's verdict beneath it,
in order, with earlier attempts left in place. The pill on each verdict is the
audit trail — **fed back to the model** or **loop ended here** — because which
verdicts the model was allowed to see is exactly the difference between the two
benchmark conditions.

A verified candidate is offered as a download. Nothing is written to
`data/routines/`, in the browser or the terminal.

Runs are slow and the page says so out loud: a tools-on agent calls
`verify_change` itself, each call runs the whole case suite against the real
database, and a single proposal can take ten minutes. Every waiting state
carries a ticking elapsed clock so that a long run is distinguishable from a
hung one.

### Models — bring your own

The registry as a two-field form, with the same two refusals as the CLI. **Test**
runs one live call and reports reachability and output format, labelled as
exactly that: competence is measured on the held-out eval set, never by a smoke
test.

### Runs — measured, not asserted

Published results if there are any, and a pending state if there are not — never
a placeholder. Underneath, the trace files every number came from: file, model,
conditions, tasks, records. If the view wants a figure that is not in the
summary, the answer is to run the report, not to average something in
JavaScript.

**Not built, and not planned:** anything that uploads a customer routine to a
hosted service. Rosetta never needs to see the customer's code, and that is
what makes it deployable air-gapped — a convenience that broke it would cost
more than it returns.

---

## What is deliberately absent

| Not built | Why |
|---|---|
| `rosetta train submit` actually submitting | data leaving the machine is the operator's decision, and air-gapped it cannot |
| applying a verified candidate to disk | a human decision on a system people depend on |
| a hosted model or hosted code | the air-gap claim is the deployment story |
| a published benchmark number | no full run has happened yet; both the site and the Runs view show pending, not a placeholder |
| a "fine-tune" button in the GUI | same reason as `train submit`: it is a spend decision and an exfiltration decision |

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
| — | `rosetta edit`, `rosetta verify`, `rosetta model`, `rosetta gui` (new) |
