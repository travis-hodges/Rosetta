---
description: Build verified training data or inspect the verifier grader
agent: rosetta
---
Manage the training workflow requested here: $ARGUMENTS

Use `python3 -m rosetta train --help` to confirm the current stages. Preserve
`data/tasks/split.lock.json` and keep evaluation tasks out of generated
training data. Never submit data to an external provider implicitly; explain
the local artifact and the explicit next step instead.
