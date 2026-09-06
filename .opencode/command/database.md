---
description: Inspect and manage persistent YottaDB state through audited change plans
agent: rosetta-agent
---
Manage database state for this request: $ARGUMENTS

For FileMan-managed content, use
`rosetta fileman --file FILE [--ien IEN] --field FIELD VALUE ...`; omitting
`--ien` creates a record through `UPDATE^DIE`, while an IEN updates through
`FILE^DIE`. A user prompt that explicitly asks to make the change authorizes
that proof-of-concept write without a second confirmation.

Use `rosetta db get REF...` for exact reads, `rosetta db plan` to pin current
values, `rosetta db preview` before applying, `rosetta db apply PLAN --yes` only
after explicit user authorization, and `rosetta db rollback RECEIPT --yes` only
when the user explicitly asks to apply that receipt's guarded inverse. The
full online snapshot remains the disaster-recovery fallback.

Never hand unvalidated text to MUMPS execution. Never perform a root-wide KILL.
Explain FileMan meaning, cross-reference risk, the active container/instance,
and whether an operation is a direct low-level global edit or an application-
level API change.
