---
description: Verify the current candidate against the baseline and explain any divergence
agent: rosetta-verify
---
Run `verify_change` on the routine we are working on: $ARGUMENTS

Report the verdict first, in one line. If it diverged, list every divergence
with the exact ref that moved, then say in plain language what that means for
real records. Do not propose a fix until the cause is named.
