# Rosetta presentation contract

Rosetta owns its TUI home screen, theme, workflow commands, and primary agent.
The underlying OpenCode engine and hosted model retain their actual names and versions.

On startup, Rosetta owns the full home logo slot with a six-row block wordmark. Its
letters form one continuous red-to-purple gradient, followed by the product loop:
**UNDERSTAND ◆ CHANGE ◆ PROVE**. Narrow terminals receive a compact colored wordmark
instead of clipping the banner.

At launch, Rosetta selects a free model advertised by the installed engine. The actual
provider and model ID remain visible. Rosetta has one selectable primary mode named
**Rosetta**. Discovery, planning, editing, execution, and proof are stages in that
session. The engine's stock `build` and `plan` modes are disabled.

## Install behavior

The installer leaves the shared engine executable intact. If it finds a verified patch
from an older Rosetta release, it restores the original executable. OpenCode can then
report its own version and use its normal update behavior. The Rosetta launcher injects
its TUI theme, logo, reference tools, primary agent, and commands without a binary patch.

```bash
scripts/rosetta-brand.py --check
scripts/rosetta-brand.py --print-logo
scripts/rosetta-brand.py --revert
```

The command enters through Rosetta's Python launcher. That launcher injects the Rosetta
theme, system map, reference tools, one primary agent, and command palette for the target
project.

## Internal compatibility names

Several upstream protocol identifiers are load-bearing and remain internal:

- the engine executable named `opencode`
- `OPENCODE_*` environment keys
- `opencode.json` and the `.opencode/` configuration directory
- the `opencode` provider key and `opencode.ai` schema addresses
- package-manager identifiers and credential storage paths

Renaming these identifiers prevents the engine from finding its configuration, provider,
or credentials. Rosetta identifies OpenCode as its engine in technical documentation and
shows the actual model name in the TUI.

## Verification

Run the presentation audit and a real terminal smoke test:

```bash
python3 -m unittest tests.test_install
npm test
rosetta --version
opencode --version
opencode models opencode
```

The legacy branding script remains for restoring installations made before this change.
New installations do not patch OpenCode.
