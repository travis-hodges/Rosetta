---
description: Turn an operational request into a controlled code and database change
agent: rosetta-agent
---
Work this operational request as a Rosetta change: $ARGUMENTS

The request is the primary object. Do not require the user to name a routine or
global before discovery.

1. **DISCOVER** — inspect routines, call paths, FileMan meaning, and current
   database values. Separate known facts from assumptions.
2. **CONTRACT** — state the intended behavior and database delta, the exact
   records allowed to move, preserved invariants, and acceptance checks.
3. **EDIT** — make the smallest code changes. For FileMan content, resolve the
   file number, IEN (for updates), and field numbers, then call
   `rosetta fileman --file FILE [--ien IEN] --field FIELD VALUE ...`. For
   non-FileMan globals, use a captured `rosetta change` plan.
4. **PREVIEW** — inspect current values before writing. For a raw global plan,
   run `rosetta db preview PLAN`.
5. **EVALUATE** — use routine equivalence only for behavior that must be
   preserved. Intentional divergence is success only when the change contract
   requires it.
6. **SHIP** — execute the requested FileMan change during this run when the
   user's original prompt asks for the change, then report the returned IEN and
   reread field values. Do not turn a request to inspect or plan into a write.

Prefer FileMan for application records so validation and cross-references run.
Use direct global changes only for content that is not managed by FileMan.
