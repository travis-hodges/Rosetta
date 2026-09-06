---
description: Take a VA VistA MUMPS change through Rosetta's full coding pipeline
agent: rosetta-agent
---
Work this developer request through the Rosetta pipeline: $ARGUMENTS

Show the stage as you enter it:

1. **DISCOVER** — start from the requested outcome; find affected routines,
   calls, FileMan records, and current database state.
2. **CONTRACT** — define expected changes, permitted writes, preserved
   behavior, acceptance cases, and rollback requirements.
3. **EDIT** — make the smallest complete code change and capture any persistent
   database operations in a Rosetta change plan.
4. **EVALUATE** — prove intended deltas and compare preserved behavior from
   identical starting state. Equivalence is not success for a new feature.
5. **SHIP** — report code, database plan, evidence, rollback, and limits.

Do not skip directly to editing. Do not call a change correct based on the
model's explanation. Never persist a plan without explicit authorization for
that named artifact. If there is no executable verdict, stop at **NO VERDICT**
and say exactly what is missing.
