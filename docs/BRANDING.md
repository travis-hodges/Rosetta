# Branding the harness as Rosetta

Rosetta adopts OpenCode whole as the agent harness — [`docs/PROJECT.md` §5](PROJECT.md)
rejects forking it, and nothing here forks it. What this does is rewrite the user-visible
branding inside the already-installed binary and add a source-aware `rosetta`
command that loads Rosetta's project workflows before it runs the harness.

```bash
scripts/rosetta-brand.py
```

That patches the installed executable and installs the launcher. Verify with
`scripts/rosetta-brand.py --check`, preview the wordmark with `--print-logo`, and undo
everything with `--revert`.

## What you get

```
$ rosetta --help
⠀                    ▄    ▄
█▀▀▄ █▀▀█ █▀▀▀ █▀▀█ ▀█▀▀ ▀█▀▀ ▄▀▀█
█    █  █ ▀▀▀█ █▀▀▀  █    █   █▀▀█
▀    ▀▀▀▀ ▀▀▀▀ ▀▀▀▀  ▀▀   ▀▀  ▀▀▀▀

Commands:
  rosetta [project]           start rosetta tui                     [default]
  rosetta run [message..]     run rosetta with a message
  ...
```

53 replacement sites in total:

| Surface | Change |
|---|---|
| Command name | `rosetta` on PATH (source-aware launcher in `~/.local/bin`, or beside `opencode`) |
| Usage output | yargs script name, every command and positional description |
| Wordmark | new `r s t a` glyphs drawn in OpenCode's own 4×3 block font — plain, shaded, and the two-glyph monogram |
| TUI home screen | shaded wordmark |
| MUMPS-first prompt | `MUMPS change…` with routine, global, and verification examples |
| TUI palette | Everforest dark preset, stacked diffs, blinking block cursor |
| Running proof | scanner pulse for executable tools; staged isolate/observe/diverge/replay flight |
| `--mini` splash | `ro` monogram plus **Rosetta**, and the `rosetta --mini -s <id>` resume hint |
| Terminal title | `Rosetta` |
| Permission dialogs | "until Rosetta is restarted", "Tell Rosetta what to do differently" |
| Crash screen | "rosetta crashed", crash-report body |
| Home-screen tips | runnable commands plus Rosetta Agent/Plan/Verify guidance |
| Misc | `/exit` description, sound-pack name, upgrade/uninstall banners, MCP and model-not-found hints |

`opencode` still works and shows the same branding: it is the same binary, and Homebrew
and npm need that name to stay.

The command intentionally does not point straight at the OpenCode executable.
It enters through Rosetta's Python launcher, which selects the current project
as the default corpus and injects the Rosetta MCP server, agents, instructions
and command palette. The branding script migrates the older direct symlink.

The injected local plugin also owns the presentation layer around proof runs:
an in-motion scanner for long executable calls, staged toasts for isolation,
observation, divergence, repair, and receipt creation, plus the `/demo` custom tool.
The pulse describes work in progress and stops before the evidence toast, so motion
can never be mistaken for a completed proof. The tool executes a bounded
live YottaDB flight and falls back to a clearly labeled recorded audit trace;
it does not simulate tool output in the model prompt.

## What is deliberately left alone

Renaming any of these breaks the tool, so the script does not touch them:

- the `opencode` **provider id** and its `OPENCODE_API_KEY` / `x-opencode-*` headers
- config discovery — `opencode.json`, `opencode.jsonc`, `~/.config/opencode/`,
  `.opencode/` (commands, agents, tools, plugins, themes)
- credential and log paths under `~/.local/share/opencode/`
- package-manager identities (`opencode-ai`, the Homebrew formula) and `opencode upgrade`
- the `opencode.local` mDNS default and the `opencode` default server username, because the
  help text for those documents a real value
- `opencode.ai` URLs, the GitHub app, and product names like OpenCode Zen / OpenCode Go
- the built-in theme id `opencode` and the ~2,800 `OpenCode` mentions in HTTP API
  descriptions, which no user reads

## How it works, and why it is safe

The binary is a Bun single-file executable: a Mach-O with the JavaScript bundle embedded
at fixed byte offsets. So every replacement is **byte-length preserving**. A shortened
string is padded with spaces at a JavaScript token boundary — after a closing quote or
paren, never inside a literal — which JavaScript ignores and the loader's offsets never
notice. The script refuses any patch that would grow the file or that changes its trailing
delimiter.

That invariant covers the **bundle**, not the file. Patching invalidates the code
signature, so the binary is re-signed ad hoc, which replaces the signature blob outright.
The stock binary ships linker-signed with 4 KB page hashes; an ad-hoc re-sign uses a larger
page size and produces a much smaller CodeDirectory. On 1.18.29 (arm64) the file came out
837,666 bytes *smaller* than the original, essentially all of it CodeDirectory
(1,117,214 → 279,498 bytes, 34,910 → 8,728 hashes).

So **do not compare file sizes to decide whether the patch is sound** — they are expected
to differ, and the difference is large enough to look alarming. Compare the sha256 values
in `opencode.exe.rosetta-brand.json` against the backup and the live binary, or run
`--check`.

Around that:

- the pristine binary is copied to `opencode.exe.rosetta-orig` before the first write, and
  every run patches *from that backup*, so it is idempotent rather than cumulative
- the new file is written to a temp path and `os.replace`d in, which leaves the sibling
  hardlink in `opencode-darwin-arm64/` untouched
- macOS gets a fresh ad-hoc signature (`codesign --force --sign -`); a modified Mach-O is
  killed on arm64 without one
- the result is smoke-tested with `--version`, and **auto-reverted** if it fails to run
- the three wordmark patches are marked required: if a future OpenCode build changes them,
  the script aborts before writing anything rather than half-patching

## After an upgrade

`brew upgrade opencode` or `npm i -g opencode-ai@latest` replaces the binary and drops
every patch. `scripts/rosetta-brand.py --check` reports `branding not applied` or
`branding stale`; re-run the script. If OpenCode has moved the strings by then, the run
aborts untouched and the patch table in the script needs updating.
