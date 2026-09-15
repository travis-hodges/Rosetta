# Rosetta presentation contract

Rosetta is the product name on every rendered surface. The terminal wordmark, window
title, help text, permission dialogs, crash screen, tips, session title, footer, provider,
model, and command examples must all say **Rosetta**.

On startup, Rosetta owns the full home logo slot with a six-row block wordmark. Each
letter uses a distinct color from the Rosetta theme, followed by the product loop:
**UNDERSTAND ◆ CHANGE ◆ PROVE**. Narrow terminals receive a compact colored wordmark
instead of clipping the banner.

The default model is presented as **Rosetta Zen**. Rosetta has one selectable primary
mode named **Rosetta**. Discovery, planning, editing, execution, and proof are stages in
that session. The engine's stock `build` and `plan` modes are disabled, and Rosetta does
not ship parallel Plan or Verify personalities.

## Install behavior

The documented installer and the source installer both run the branding step when the
terminal engine is present. The patch is reversible, byte-length preserving, smoke-tested,
and re-signed on macOS. A pristine backup is written beside the executable before the
first change. Uninstall restores that backup before removing Rosetta's files.

```bash
scripts/rosetta-brand.py
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

They are implementation addresses rather than display names. Renaming them prevents the
engine from finding its configuration, provider, or credentials. Rosetta's tests permit
these tokens only in internal wiring and reject them from product-facing copy.

## Verification

Run the presentation audit, branding tests, and a real terminal smoke test:

```bash
python3 scripts/rosetta-brand.py --check
python3 -m unittest tests.test_install
npm test
rosetta --version
```

An engine upgrade replaces the patched executable. Re-run the Rosetta installer or
`scripts/rosetta-brand.py`; required patches fail before writing if the new bundle no
longer matches the safe replacement table.
