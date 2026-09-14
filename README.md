# Rosetta

Coding models have uneven competence in unfamiliar technical environments. Rosetta
adds repository-provided technical references to OpenCode so the agent can look up
missing knowledge, edit real software, and check the result against its runtime.

- Four real tools: source inventory, search, incremental reading, and examples.
- One-command import into local `rosetta.json` for manuals, SDKs, APIs and internal documentation.
- Deterministic lexical retrieval with source, heading, line and hash provenance.
- Real MUMPS payment demo with YottaDB execution and output/database checks.
- A guarded onboarding prompt when a task truly needs an unfamiliar language source.

[Setup, configuration and demo commands](docs/REFERENCES.md).

```sh
bash scripts/install.sh --bin-dir "$HOME/.local/bin"
rosetta /path/to/project
```

Requires Python 3.11+ and an installed/configured OpenCode harness. Local retrieval is
offline; hosted model access needs a connection. The MUMPS demo additionally needs
the existing Docker/YottaDB runtime. [Runtime setup](docs/REFERENCES.md#real-mumps-demo).

## Run the MUMPS demo

```sh
export ROSETTA_CONTAINER=rosetta-reference-verify
bash scripts/bootstrap.sh
python3 -m rosetta.core.selftest
python3 examples/payments/verify.py
rosetta examples/payments
```

Ask the agent to make payments safe to retry without crediting an account twice.
The repository supplies authoritative documentation and repeatable tests. It starts
with 10 passing regression cases and four failing feature cases. The agent must
choose and implement its own patch. [Complete task and setup](docs/REFERENCES.md#real-mumps-demo).

The recorded acceptance run includes reference lookup, actual source edits, a real
failure, repair and 15 passing final runtime cases. A second non-MUMPS source was
registered and retrieved using the same tools. [Inspect the evidence](docs/evidence/reference-demo).

## Existing runtime capabilities

The VistA/FileMan tools remain available through `rosetta fileman`, `rosetta db`,
`rosetta eval`, `rosetta verify` and the benchmark CLI. The frozen core API and split
lock are unchanged. [Usage](docs/USAGE.md) · [Architecture](docs/PROJECT.md).

`rosetta demo` is the historical recorded verifier replay. To watch a new coding
agent task, open `examples/payments` as above. No benchmark improvement is claimed.
The finite test suite does not establish safety under concurrent writers or for
untested external side effects.

## Add your own knowledge

Attach or point Rosetta to a UTF-8 Markdown, text, or reStructuredText file or directory:

```sh
rosetta references add /path/to/manuals --language MUMPS --project . \
  --origin 'https://docs.yottadb.com/ProgrammersGuide/'
```

Rosetta creates or updates `rosetta.json`, imports material from outside the project,
and validates retrieval before reporting success. If a task needs an unfamiliar language
and no adequate source exists, the agent asks whether you want to provide the material or
authorize it to find primary documentation.
[Configuration and retrieval guide](docs/REFERENCES.md) ·
[Non-MUMPS registration example](examples/widget-sdk/rosetta.json).

Code is Apache-2.0. Bundled YottaDB documentation is distributed separately under
GNU FDL 1.3 or later, with attribution and license text in
`examples/payments/references/`.
