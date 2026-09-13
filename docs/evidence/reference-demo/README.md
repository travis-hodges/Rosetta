# Observed acceptance run — September 13, 2026

These are recorded development artifacts from real OpenCode/Rosetta sessions.
The website does not run them live. The base demo remains unsolved so another
operator can give it a fresh task; no recorded patch is loaded by the agent tools.

The first session inspected three `.m` files, invoked `reference_search` four
times against the repository-provided YottaDB manuals, edited `PAYMENT.m`, and
ran actual YottaDB checks: 10 regression cases and four retry cases passed.
It also wrote and executed five extra edge cases, which passed. Those temporary
cases appear in the tool log, and were removed by the agent afterward.

Review then found an undeclared caller-local delimiter dependency (`U`). A new
case supplied `U="|"` while the documented receipt format remained caret-delimited.
In a second session, the agent ran that case and observed a real 0/1 failure,
consulted the reference system, changed the three `$PIECE` calls to use a literal
caret, and reran the suite: 1/1 delimiter, 4/4 retry and 10/10 regression cases passed.
An independent run afterward confirmed all 15 cases against the final source hash.

- `payments-tools.jsonl`: native tool calls/results from the feature implementation.
- `payments-repair-tools.jsonl`: native tool calls/results from the observed failure and repair.
- `repair-checks.json`: actual runner results extracted from the repair session.
- `independent-final-checks.json`: independently executed final checks.
- `baseline.json`: original project's 10 passing regression cases.
- `feature-before.json`: original project's four failing feature cases.
- `verified-PAYMENT.m` and `agent-change.diff`: final agent-authored source and diff.
- `delimiter-cases.json`: the additional failing-then-passing case.
- `widget-sdk-tools.jsonl`: a separate real agent session that used sources, search
  and read on a non-MUMPS SDK reference registered through the same configuration.

Logs retain actual temporary paths, document hashes and tool timestamps. Only
native action events are included; no private reasoning is published. The model
was the checkout's configured `opencode/big-pickle`; it was not fine-tuned for this task.

Runtime: existing `worldvista/vehu` image, fresh quiescent container
`rosetta-reference-verify`. All execution used the unchanged Rosetta core, real
YottaDB and `clean_state()` rollback. No runtime mock, canned answer, fixed patch,
or replay was used in these sessions. The old default verification container had
unrecovered state; it was left intact. This is finite-case engineering evidence,
not a comparison benchmark or proof of behavior under concurrent writers.
