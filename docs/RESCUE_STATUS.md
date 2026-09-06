# Rescue verification record — September 6, 2026

Rosetta now provides a working terminal entry point, configurable models and
corpora, executable MUMPS evaluation, and benchmark traces with checked provenance.
The replacement animated website was developed in the separate **Rosetta website rebuild**
task and integrated into this checkout. No changes have been committed or deployed.

## Delivered

- Dependency-free launcher (`scripts/install.sh`), CLI doctor/code/models/eval/bench/report/
  demo/tools commands, portable harness configuration, and eight MCP tools.
- Explicit model, project, corpus, suite, and dictionary selection. Custom corpora do not
  silently inherit the VA dictionary or contact its container. VA FOIA remains optional.
- Nonzero failure exits, bounded prompt/model requests, input-preserving eval output,
  and immediate persistence of completed benchmark attempts.
- Candidate generation with tools disabled and a private working directory enforced via
  both the harness `--dir` and environment `PWD`; confirmed in actual harness logs.
- Protocol-2 trace fingerprints and reference-manifest checks; rejection of old/mixed
  protocols, runs, models, budgets, changed tasks, and unscoreable comparisons. Reports
  use bounded repair results instead of treating feedback retries as independent pass@3.
- Capture of statically reachable callee writes and output, including ZWRITE; rejection
  of unsafe transaction commands, unbounded writes, unsupported capture depths, and MERGE
  under trigger capture. Subtree deletion under trigger capture makes a case inconclusive;
  leaf deletion and complete query capture remain supported.
- Per-runtime trigger ownership, final values at touched nodes, void/truncation/restart
  handling, and restoration of reference source after candidate compile failures.
- Real worker termination inside Docker, PID/token checks, independent-worker protection,
  and shared lifetime locks that prevent database restore under active Rosetta workers.
- Bootstrap database write/rollback validation and native MUPIP backups with independent
  saved journal state. Restore validates completed backups, takes an exclusive lock,
  runs rundown, stages region files, replaces them, and checks the restored runtime.
- Website narrative, animated sculpture and scroll story, illustrative patch interaction,
  keyboard language tabs, mobile menu, copy commands, motion controls, and cited government
  context. The old frontend was replaced using the supplied website template as the start.

## Verification

**455 Python tests covered across suites**, plus **12 website tests and production build**.
The final core pass ran 65 tests in 155.011 seconds with one opt-in snapshot skip. That
snapshot was exercised separately against the full four-region, approximately 3.4 GB
fixture: a committed probe created after the backup disappeared after restore, and a
real routine still executed correctly. The latest core log is
`results/rescue-core-verified.log`; snapshot evidence is `results/rescue-snapshot-fixed.log`.
The latter also records an initially incorrect ZWRITE test abbreviation; the corrected
ZWRITE test passes in the final core run.

Other evidence:

- Four real worker lifecycle tests passed again after adding the shared lock. A live
  isolated lock probe checked retention, release, startup refusal, and restart.
- The final parser/tools/benchmark pass ran 146 tests; the benchmark suite contains 62.
- CLI/installer tests passed after aligning external-project PWD. The launcher installed
  into `.rosetta/bin/rosetta` successfully ran from `/tmp`.
- Real source export preserved two exact routine files and ignored a non-source file.
  The FileMan export adapter confirmed transaction level 1 and rollback to 0.
- Runtime doctor passed a real comparison; bootstrap passed actual write/rollback checks.
- The website task checked desktop, tablet, narrow mobile, and landscape mobile views,
  menu/keyboard/copy/demo/motion interactions, no horizontal overflow, and clean console.
  Reduced-motion scheduling passed its automated check; OS-level emulation was unavailable.
- Python compilation, shell syntax, and whitespace checks passed. The frozen interface
  and `data/tasks/split.lock.json` remain unchanged. No new packages were installed.

## Authentic model evidence

Original RGUTUU and LRLRRVF eval references each passed all six self-comparison cases,
and their original injected defects were detected with no void cases. Under protocol
`isolated-harness-feedback/2`, `opencode/mimo-v2.5-free` reproduced the original LRLRRVF
reference exactly, passing all six original cases. Actual model events contained no tool
calls, and harness logs confirmed its private working directory.

The matching feedback-condition request exceeded its 120-second provider deadline.
There is **no comparable model benchmark score**. The report correctly refuses publication
with the actual exclusion reason. Evidence is in `results/rescue-va/mimo-smoke/`; original
preflights are in `results/rescue-va/preflight-current.json`. These are local ignored
artifacts. The run used a shared container, so durations are not timing-performance evidence.
Earlier protocol-1 traces are explicitly invalidated and must not support comparisons.

## Boundaries

MUMPS/YottaDB is the implemented runtime. Models and local endpoints are configurable;
trained specialist weights, additional language runtimes, and a complete model comparison
are not delivered. Benchmark feedback comes from the harness; it does not measure autonomous
MCP use. Finite cases and supported observables are not a general proof of program safety.
Caller-local state and external side effects remain outside the frozen result contract.

Use a dedicated verification container. Locks coordinate Rosetta workers, not unrelated
external YottaDB processes. Old raw-copy snapshots are not accepted by the repaired restore
path. Full backups can take several minutes under emulation. The native backup approach
follows the [YottaDB MUPIP documentation](https://docs.yottadb.com/AdminOpsGuide/dbmgmt.html#backup).
