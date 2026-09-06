---
description: Take a VA VistA MUMPS change through Rosetta's full coding pipeline
agent: rosetta-agent
---
Work this developer request through the Rosetta pipeline: $ARGUMENTS

Show the stage as you enter it:

1. **UNDERSTAND** — read and parse the routine, trace calls, resolve globals.
2. **CHANGE** — make the smallest complete source change.
3. **PROVE** — run the baseline and candidate from identical state and compare
   output plus global database state.
4. **EXPLAIN** — give the verdict, affected VA data, cases covered, and limits.

Do not skip directly to editing. Do not call a change correct based on the
model's explanation. If there is no executable verdict, stop at **NO VERDICT**
and say exactly what is missing.
