---
description: Run Rosetta's live YottaDB proof flight with a recorded fallback
agent: rosetta-verify
subtask: true
---
Call `rosetta_showcase` exactly once. It runs a short, fresh YottaDB proof on a
real VistA MUMPS routine: reject a bad candidate, repair it, replay it, and save
content-addressed receipts. If the live runtime cannot finish, it automatically
falls back to the committed AJETIU2 audit trace. Do not replace the tool call
with a shell command or a prose simulation.

Show its provenance badge, divergence, repair, and proof receipt without
changing their meaning. Then explain in two sentences why the plausible
candidate was wrong. A deterministic candidate generator is not a model
benchmark; say so plainly.
