# Rosetta

**A terminal coding agent for obscure languages, with executable evidence for its changes.**

Rosetta combines OpenCode's coding workflow, your choice of model, and a runtime that checks
produced code. Connect a specialist or general model, inspect and modify a codebase, then
run evaluations against the real interpreter. Benchmark models using repeatable tasks and
saved execution verdicts.

The implemented runtime is **MUMPS under YottaDB**. COBOL, JOVIAL, and CMS-2 are future
runtime integrations. You can use OpenCode to edit other languages, but Rosetta does not
claim to execute or verify them yet. No trained specialist weights ship in this repository.

VA FOIA VistA is an optional public test corpus. It is not the application architecture:
you can select your own MUMPS source, case suites, dictionary, and runtime container.

## Start the terminal

From this checkout, with Python 3.11+ and OpenCode installed:

```bash
python3 -m rosetta doctor
python3 -m rosetta models
python3 -m rosetta code
```

Choose a configured model and an external project:

```bash
python3 -m rosetta code /path/to/project --model provider/model --corpus /path/to/routines
```

Add `--prompt "Explain the routine before changing it"` to run a single request.
`ROSETTA_MODEL` supplies the default coding model. Provider credentials and local model
endpoints are configured through OpenCode; see [model setup](docs/USAGE.md#models).
Install a launcher without pip or downloading packages:

```bash
bash scripts/install.sh --bin-dir "$HOME/.local/bin"
"$HOME/.local/bin/rosetta" doctor
```

Add that directory to your PATH to use `rosetta` from any project. The installer preserves
the working directory and refuses to overwrite an existing launcher; keep the source
checkout at its installation path. Prompt requests default to a 300-second deadline;
use `code --prompt "..." --timeout 600` to change it.

Python's runtime path has no third-party dependencies. Optional editable installation
(`python3 -m pip install -e .`) supplies the shorter `rosetta` command and requires
standard setuptools build tooling.

The launcher constructs paths for the current checkout in memory. It does not write a
machine-specific path into `opencode.json`. `scripts/opencode-setup.sh --check` checks the
MCP handshake and OpenCode connection; `python3 -m rosetta tools` starts the stdio server.

## Execute and evaluate

Execution requires a dedicated YottaDB environment. The optional WorldVistA bootstrap
creates `rosetta-verify`; it is a multi-GB external prerequisite, not part of Python setup.
If using that fixture, run `bash scripts/bootstrap.sh`. For a configured runtime:

```bash
python3 -m rosetta doctor --runtime
python3 -m rosetta.core.selftest
python3 -m rosetta eval \
  --baseline examples/mumps/ROSAGE.m \
  --candidate examples/mumps/ROSAGE.candidate.m \
  --cases examples/mumps/cases.json
```

The example deliberately changes an age boundary. Evaluation should exit **1**, naming
the differing output for input `18`. Use the baseline as both files to get a passing
comparison. Exit **2** means configuration, compilation, or execution could not complete.

Verification compares stdout, errors, and captured persistent global state under rollback.
An invalid or truncated execution never earns an equivalence verdict. Unbounded write
sets or uninspectable callees are rejected. A pass covers the supplied cases and supported
observables; it is not a proof for all inputs, caller-local variables, or external effects.

## Benchmark a model

```bash
python3 -m rosetta bench --model provider/model --limit 1 --attempts 2
python3 -m rosetta report --print-only
```

Use `--taskset /path/to/tasks.json` and `--out /path/to/traces` for your own tasks. The
runner saves JSONL attempts and independently grades generated source. Candidate generation
runs outside the answer repository with tools disabled. `baseline` gets no verifier
feedback; `scaffolded` gets harness feedback between attempts. **This is a feedback-loop
comparison, not a measurement of autonomous MCP tool use.** The coding terminal itself
has MCP tools available.

The report requires aligned conditions and the frozen eval split. Synthetic smoke tests,
recorded demos, failed executions, and arbitrary external task sets do not become published
benchmark claims. No full benchmark result is claimed here.

## Repository map

- `rosetta/cli.py`: terminal launcher, doctor, evaluation commands.
- `rosetta/core/`: YottaDB execution, rollback, capture, comparison.
- `rosetta/tools/`: source analysis and eight MCP tools.
- `rosetta/bench/`, `rosetta/mutate/`, `rosetta/train/`: tasks, scoring, data preparation.
- `rosetta/demo/`: clearly labelled recorded and live demonstration paths.
- `index.html`, `src/`: animated public website, rebuilt and integrated from its separate task.
- `orchestration/`: development infrastructure; not the product.

Run `python3 -m unittest discover -s tests -v`. Real runtime tests run when their container
is reachable; skipped runtime tests are not execution evidence. `npm test` and
`npm run build` check the independent website.

[Usage and configuration](docs/USAGE.md) · [Architecture and historical research](docs/PROJECT.md)
· [Contributor contract](AGENTS.md) · [Build infrastructure](docs/ORCHESTRATION.md)
