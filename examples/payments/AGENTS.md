# Payment ledger demo

This is a local MUMPS/YottaDB payment ledger with synthetic accounts only.
The service, storage and reporting routines are in routines/. Technical sources
and runtime/test commands are declared in rosetta.json. Use the configured
references when the language, API or project semantics are uncertain.

Run `python3 verify.py` for existing behavior. New engineering tasks can add a
separate JSON case suite via `python3 verify.py --cases path.json`.
Checks use the real Rosetta runtime and roll back every case. Do not call Docker
or YottaDB directly; verify.py uses rosetta.core. Do not modify verify.py or the
existing acceptance cases to make a source change pass. Add tests for new behavior.
Do not commit or push from this demo; the caller reviews source changes.
