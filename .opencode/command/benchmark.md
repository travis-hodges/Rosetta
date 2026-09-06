---
description: Run or inspect a Rosetta model benchmark
agent: rosetta-agent
---
Manage the benchmark workflow requested here: $ARGUMENTS

Use `python3 -m rosetta bench --help` to confirm current flags. Before starting
a model run, state the model, task set, task limit, attempt budget, and output
path; ask only for any value that is genuinely missing. Preserve the frozen
evaluation split. Afterward, distinguish completed, failed, and unscoreable
attempts and do not present a partial run as a published benchmark.
