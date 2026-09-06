# Branding the harness as Rosetta

Rosetta adopts OpenCode whole as the agent harness — [`docs/PROJECT.md` §5](PROJECT.md)
rejects forking it, and nothing here forks it. What this does is rewrite the user-visible
branding inside the already-installed binary and add a `rosetta` command that runs it.

```bash
scripts/rosetta-brand.py
```

That patches the installed executable and installs the command. Verify with
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

49 replacement sites in total:

| Surface | Change |
|---|---|
| Command name | `rosetta` on PATH (symlink in `~/.local/bin`, or beside `opencode`) |
| Usage output | yargs script name, every command and positional description |
| Wordmark | new `r s t a` glyphs drawn in OpenCode's own 4×3 block font — plain, shaded, and the two-glyph monogram |
| TUI home screen | shaded wordmark |
| `--mini` splash | `ro` monogram plus **Rosetta**, and the `rosetta --mini -s <id>` resume hint |
| Terminal title | `Rosetta` |
| Permission dialogs | "until Rosetta is restarted", "Tell Rosetta what to do differently" |
| Crash screen | "rosetta crashed", crash-report body |
| Home-screen tips | the ten that name a runnable command |
| Misc | `/exit` description, sound-pack name, upgrade/uninstall banners, MCP and model-not-found hints |

`opencode` still works and shows the same branding: it is the same binary, and Homebrew
and npm need that name to stay.

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
