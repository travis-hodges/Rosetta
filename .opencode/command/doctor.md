---
description: Check Rosetta, its tools, and optional runtime readiness
agent: rosetta-verify
subtask: true
---
Run `python3 -m rosetta doctor` and explain the result briefly. If the user
included a specific concern, use it to focus the explanation: $ARGUMENTS

Do not describe the executable verifier as available unless the runtime probe
actually passed.
