---
description: Evaluate a candidate against a trusted baseline and case suite
agent: rosetta
---
Help the user run Rosetta's standalone evaluator for: $ARGUMENTS

Identify the baseline source, complete candidate source, and JSON case suite.
Then run `python3 -m rosetta eval --baseline BASELINE --candidate CANDIDATE
--cases CASES`, adding `--routine NAME` only if filename inference is wrong.
Report the verdict before interpretation. Never invent missing paths or cases.
