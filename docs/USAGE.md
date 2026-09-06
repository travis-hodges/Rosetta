# Terminal workflow

Run commands from the source checkout, or install a dependency-free launcher with
`bash scripts/install.sh --bin-dir "$HOME/.local/bin"`. The installer never overwrites an
existing file or changes your shell settings. Keep the checkout and interpreter in place.
Optional pip editable installation is also supported by the package metadata.
`python3 -m rosetta --help` lists commands; each command has its own `--help`.

## Models

Use `python3 -m rosetta models` to see models available to the installed OpenCode.
Choose `--model provider/model` when coding or benchmarking. Rosetta does not download
weights, train a model, or start a model server implicitly.

For a specialist served through an OpenAI-compatible local endpoint, add a provider to
your own OpenCode configuration (replace the endpoint and served model ID):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "specialist": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Local language specialist",
      "options": { "baseURL": "http://127.0.0.1:8080/v1" },
      "models": { "your-served-model-id": { "name": "Language specialist" } }
    }
  }
}
```

Then use `--model specialist/your-served-model-id`. The server must already be running,
and its model must support the chosen workflow. A configured name does not establish
model quality; measure it. Provider setup follows the official
[OpenCode providers documentation](https://opencode.ai/docs/providers/#custom-provider)
and [configuration documentation](https://opencode.ai/docs/config/).

Hosted providers receive the code sent to them. Local operation requires a local model
endpoint and installed local runtime; selecting a hosted model is not an air gap.

## Bring a corpus

Routine source files are named `NAME.m`, one routine per file. Use `--corpus` with `code`,
or configure the MCP process through environment variables:

| Variable | Meaning |
|---|---|
| `ROSETTA_CORPUS_DIR` | Directory of your routine sources; an invalid explicit path fails |
| `ROSETTA_TASKS_DIR` | Directory of task JSON/JSONL and default `suites/` |
| `ROSETTA_SUITES_DIR` | Override reusable per-routine case-suite directory |
| `ROSETTA_FILEMAN_CACHE` | Optional compatible gzipped FileMan dictionary, or `off` |
| `ROSETTA_TOOLS_CONTAINER` | Explicit optional container source fallback; disabled by default |
| `ROSETTA_TOOLS_ROUTINE_DIR` | Source directory inside that optional container |
| `ROSETTA_CONTAINER` | Dedicated execution container; default `rosetta-verify` |
| `ROSETTA_INSTANCE` | Instance user/layout; default `vehu` |
| `ROSETTA_DOCKER` | Container CLI executable; default `docker` |

When a custom corpus is selected, Rosetta does not apply the bundled VA dictionary unless
you explicitly select a dictionary. The bundled routines and task split remain as public
research fixtures. New corpora must not overwrite `data/tasks/split.lock.json`.

The current execution adapter expects the instance layout in `rosetta/core/config.py`.
The bootstrap configures WorldVistA in that layout. Other MUMPS installations need a
matching configured environment; other languages need a runtime adapter.

## Evaluation cases

Pass a JSON array or `{ "cases": [...] }` with one or more execution specifications:

```json
{"cases":[
  {"routine":"ROSAGE","entry":"$$ELIGIBLE","args":["18"],
   "locals_in":{},"globals_in":{},"timeout_s":10}
]}
```

`$$` explicitly invokes an extrinsic returning a value. Initial globals and locals must
map names to strings. Use a disposable verification database, never a production service.
The verifier restores transactional state after each case. Unsupported dynamic writes,
transaction commands (including in inspected callees), and incomplete captures fail closed.
Large-root trigger capture refuses MERGE and references beyond its configured depth.
A subtree deletion makes an execution inconclusive; leaf deletion remains supported.
Subtree operations need a complete query capture plan.

`eval` compares a candidate with a reference. For an intentional behavior change, supply
a trusted updated reference and cases expressing the desired behavior; equivalence to the
old version cannot prove a new feature is correct.

## Benchmark interpretation

The default task file contains mutation-repair tasks. External task files have a `tasks`
array with `task_id`, `routine`, `baseline_src`, `mutated_src`, `operator`, and `cases`.
Only the grader sees `baseline_src`; candidate generation gets the mutant and request.
Both conditions have tools disabled in a private workspace. The scaffolded condition sees
specific grader feedback after failed attempts. The recorded `harness_verify_equivalence`
event is not an agent MCP invocation.

The CLI's `--attempts` bounds the repair loop. Provider failures and invalid execution are
reported separately from an ordinary incorrect candidate. Reports enforce the committed
split and comparable scoreable tasks before publishing numbers. Existing traces generated
before the isolation fix must not be used to claim a clean model comparison.

Run `python3 -m rosetta demo --help` for the demo harness. Scripted/replayed demos illustrate
the verifier and are never evidence about a live model's ability.

## Bounded runs and provenance

A single coding prompt has a 300-second timeout by default; use `code --timeout` to set
a different positive deadline. Timeouts exit 124; a failed environment exits nonzero.
`eval --out` refuses to overwrite any source or case input, including filesystem aliases.

Benchmark model calls have a separate 120-second default deadline, configurable with
`bench --model-timeout`. Each completed attempt is written immediately. New traces carry
a protocol marker, task content fingerprints, and attempt budgets. Reporting refuses
legacy or mixed protocols, mismatched models in the comparison, and changed task content.
Partial coverage is explicitly labelled in the detailed and terminal reports.

## Runtime startup and cleanup

The optional bootstrap tests an actual database write and rollback after checking routines.
Its first database attachment can take up to three minutes under emulation. A routine-only
smoke test does not establish that a cold database is ready. Worker timeouts terminate the
matching M process inside the container and recover with a fresh process. Each runtime owns
its trigger names and process identity, so cleanup does not delete another runtime's triggers
or signal another worker. Use a dedicated container for repeatable timing measurements.

Snapshots use YottaDB's native database backup with journal links disabled in the saved
copy. Restore accepts only completed snapshots, runs database rundown, stages all region
files, then replaces them while holding an exclusive lock. New Rosetta workers hold
shared locks for their lifetime. These locks do not control unrelated external YottaDB
processes; the verification container must remain dedicated. Old raw-copy snapshots are
not accepted. Full database round trips can take several minutes under emulation.
