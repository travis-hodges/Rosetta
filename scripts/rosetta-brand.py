#!/usr/bin/env python3
"""Rebrand the installed OpenCode binary as Rosetta.

OpenCode is adopted whole as the agent harness (docs/PROJECT.md §5 rejects
forking it). This script does not fork anything: it rewrites the user-visible
branding inside the already-installed single-file binary, and installs the
source-aware `rosetta` launcher that injects Rosetta's project workflows.

Every edit is a byte-length-preserving in-place replacement inside the embedded
JavaScript bundle. Shortened strings are padded with spaces at a JS token
boundary -- never inside a string literal -- so the bundle's byte offsets stay
exactly where Bun's loader expects them. The original binary is copied to
<binary>.rosetta-orig before the first write, and `--revert` puts it back.

The FILE, however, does not keep its original size, and comparing sizes is not
a valid way to check whether the patch is sound. Patching invalidates the code
signature, so the binary is re-signed ad hoc afterwards, and that replaces the
signature blob wholesale. The stock binary ships linker-signed with 4 KB page
hashes; an ad-hoc re-sign uses a larger page size, so the CodeDirectory shrinks
substantially -- measured on 1.18.29 (arm64) the file lost 837,666 bytes, of
which 837,716 is CodeDirectory (1,117,214 -> 279,498, 34,910 -> 8,728 hashes).
The bundle itself is unchanged in length.

To verify a patch, compare the sha256 values recorded in
<binary>.rosetta-brand.json against the backup and the live binary, or run
`--check`. Do not compare file sizes.

Only cosmetic strings are touched. Provider ids, config paths (~/.config/opencode,
opencode.json, .opencode/), package names, mDNS defaults, HTTP headers and auth
paths are deliberately left alone: renaming those breaks the tool.

Usage:
  scripts/rosetta-brand.py                  # patch + install the `rosetta` command
  scripts/rosetta-brand.py --check          # report state, change nothing
  scripts/rosetta-brand.py --print-logo     # preview the wordmark
  scripts/rosetta-brand.py --revert         # restore the original binary
  scripts/rosetta-brand.py --bin-dir DIR    # where to put the `rosetta` command
  scripts/rosetta-brand.py --no-command     # patch only, no `rosetta` command

Re-run it after `brew upgrade opencode` / `npm i -g opencode-ai@latest`; an
upgrade replaces the binary and drops every patch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

BRAND = "Rosetta"
brand = "rosetta"
STATE_SUFFIX = ".rosetta-brand.json"
BACKUP_SUFFIX = ".rosetta-orig"
REPO_ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------- the font
#
# OpenCode's wordmark is a 4-column, 3-row block font with a 1-row ascender
# strip above it. Two variants of the same glyphs are embedded in the binary:
#
#   plain   -- what the CLI prints when stdout/stderr is not a TTY
#   marked  -- the TTY/TUI version, where three characters are not literal:
#                "_"  counter, filled with the shadow background
#                "^"  half stroke drawn over that fill
#                "~"  shadow-coloured half stroke
#
# o/p/e/n/c/d are OpenCode's own glyphs and are only used to locate the existing
# literals. r/s/t/a are new, drawn in the same idiom.
#
# A marker only ever describes an *enclosed* counter, so a glyph that has none
# carries no markers and its marked form is just its plain form -- that is why
# r, s and t are identical in both tables. Marking r's open right side (it was
# once "█___" over "▀~~~") painted a shadow rectangle into the page and gave the
# letter a bottom bar it does not have, which read as a filled blob rather than
# an r. The invariants that catch this are checked in tests/test_install.py.

PLAIN = {
    " ": ("    ", "    ", "    ", "    "),
    "o": ("    ", "█▀▀█", "█  █", "▀▀▀▀"),
    "p": ("    ", "█▀▀█", "█  █", "█▀▀▀"),
    "e": ("    ", "█▀▀█", "█▀▀▀", "▀▀▀▀"),
    "n": ("    ", "█▀▀▄", "█  █", "▀  ▀"),
    "c": ("    ", "█▀▀▀", "█   ", "▀▀▀▀"),
    "d": ("   ▄", "█▀▀█", "█  █", "▀▀▀▀"),
    "r": ("    ", "█▀▀▄", "█   ", "▀   "),
    "s": ("    ", "█▀▀▀", "▀▀▀█", "▀▀▀▀"),
    "t": (" ▄  ", "▀█▀▀", " █  ", " ▀▀ "),
    "a": ("    ", "▄▀▀█", "█▀▀█", "▀▀▀▀"),
}

MARKED = {
    " ": ("    ", "    ", "    ", "    "),
    "o": ("    ", "█▀▀█", "█__█", "▀▀▀▀"),
    "p": ("    ", "█▀▀█", "█__█", "█▀▀▀"),
    "e": ("    ", "█▀▀█", "█^^^", "▀▀▀▀"),
    "n": ("    ", "█▀▀▄", "█__█", "▀~~▀"),
    "c": ("    ", "█▀▀▀", "█___", "▀▀▀▀"),
    "d": ("   ▄", "█▀▀█", "█__█", "▀▀▀▀"),
    "r": ("    ", "█▀▀▄", "█   ", "▀   "),
    "s": ("    ", "█▀▀▀", "▀▀▀█", "▀▀▀▀"),
    "t": (" ▄  ", "▀█▀▀", " █  ", " ▀▀ "),
    "a": ("    ", "▄▀▀█", "█^^█", "▀▀▀▀"),
}

# The small two-glyph mark: "oc" becomes "ro".
OLD_MARK_LEFT = ("    ", "█▀▀▀", "█_^█", "▀▀▀▀")
OLD_MARK_RIGHT = MARKED["o"]
NEW_MARK_LEFT = MARKED["r"]
NEW_MARK_RIGHT = MARKED["o"]

BLANK_LEAD = "⠀"  # braille blank: keeps the all-space ascender row alive


def rows(table: dict[str, tuple[str, ...]], word: str) -> list[str]:
    """Render `word` as four joined rows of the block font."""
    return [" ".join(table[ch][r] for ch in word) for r in range(4)]


def with_blank_lead(lines: list[str]) -> list[str]:
    out = list(lines)
    if out[0].startswith(" "):
        out[0] = BLANK_LEAD + out[0][1:]
    return out


# The colours the TUI actually paints the shaded wordmark with, read off the
# escape stream of a running home screen: a dim half and a bright half, each
# with its own shadow tone. Reproducing them here is the only way to preview
# the marked glyphs -- printing them raw shows "_^~", not what a user sees.
_DIM = ("\x1b[38;2;128;128;128m", "\x1b[48;2;40;40;40m", "\x1b[38;2;40;40;40m")
_BRIGHT = ("\x1b[38;2;238;238;238m", "\x1b[48;2;67;67;67m", "\x1b[38;2;67;67;67m")
_OFF = "\x1b[0m"


def shaded(half: list[str], palette: tuple[str, str, str], color: bool) -> list[str]:
    """Resolve one half's marked rows the way the TUI paints them.

    Without colour the markers are resolved to their shapes only, which is
    still enough to see a glyph that has grown a stroke it should not have.
    """
    ink, fill, shadow = palette
    out = []
    for row in half:
        parts = []
        for ch in row:
            if ch == "_":
                parts.append(f"{fill} {_OFF}" if color else " ")
            elif ch == "^":
                parts.append(f"{ink}{fill}\u2580{_OFF}" if color else "\u2580")
            elif ch == "~":
                parts.append(f"{shadow}\u2580{_OFF}" if color else "\u2580")
            else:
                parts.append(f"{ink}{ch}{_OFF}" if color else ch)
        out.append("".join(parts))
    return out


def print_logo(color: bool) -> None:
    """Both wordmarks: the plain CLI one, then the shaded TUI/home-screen one."""
    print("plain (non-TTY CLI)")
    for line in with_blank_lead(rows(PLAIN, brand)):
        print("  " + line.replace(BLANK_LEAD, " "))
    print()
    print("shaded (CLI + TUI home)")
    left = shaded(rows(MARKED, "ro"), _DIM, color)
    right = shaded(rows(MARKED, "setta"), _BRIGHT, color)
    for a, b in zip(left, right):
        print("  " + a + " " + b)
    print()
    print("monogram (mini splash)")
    for line in shaded(list(NEW_MARK_LEFT), _DIM, color):
        print("  " + line)


def jsstr(text: str) -> bytes:
    """Escape like the bundler does: ASCII verbatim, everything else \\uXXXX."""
    body = "".join(c if 32 <= ord(c) < 127 else "\\u%04X" % ord(c) for c in text)
    return b'"' + body.encode() + b'"'


def jsarray(lines: list[str]) -> bytes:
    return b"[" + b",".join(jsstr(line) for line in lines) + b"]"


def split_literal(left: list[str], right: list[str]) -> bytes:
    return b"{left:" + jsarray(left) + b",right:" + jsarray(right) + b"}"


# ------------------------------------------------------------------ the patches
#
# Each patch is (name, old, new, required). `new` must be no longer than `old`
# and must end on the same delimiter, so the space padding lands between JS
# tokens rather than inside a literal.

def build_patches() -> list[tuple[str, bytes, bytes, bool]]:
    p: list[tuple[str, bytes, bytes, bool]] = []

    def add(name: str, old: str | bytes, new: str | bytes, required: bool = False) -> None:
        o = old.encode() if isinstance(old, str) else old
        n = new.encode() if isinstance(new, str) else new
        p.append((name, o, n, required))

    # --- wordmarks -----------------------------------------------------------
    add(
        "wordmark (plain, non-TTY CLI)",
        jsarray(with_blank_lead(rows(PLAIN, "opencode"))),
        jsarray(with_blank_lead(rows(PLAIN, brand))),
        required=True,
    )
    add(
        "wordmark (shaded, CLI + TUI home)",
        split_literal(rows(MARKED, "open"), rows(MARKED, "code")),
        split_literal(rows(MARKED, "ro"), rows(MARKED, "setta")),
        required=True,
    )
    add(
        "monogram (mini splash)",
        split_literal(list(OLD_MARK_LEFT), list(OLD_MARK_RIGHT)),
        split_literal(list(NEW_MARK_LEFT), list(NEW_MARK_RIGHT)),
        required=True,
    )
    # The mini splash draws the right-hand glyph only; point it at the "r".
    add("mini splash glyph (entry + exit)", "so.right.slice(1),t=1", "so.left.slice(1),t=1")

    # --- command name --------------------------------------------------------
    add("cli script name", '.scriptName("opencode")', '.scriptName("rosetta")', required=True)
    add("usage/logo split", 'startsWith("opencode ")', 'startsWith("rosetta ")', required=True)

    # --- command list --------------------------------------------------------
    for text in [
        "opencode auth provider",
        "upgrade opencode to the latest or a specific version",
        "uninstall opencode and remove all related files",
        "starts a headless opencode server",
        "attach to a running opencode server",
        "attach to a running opencode server (e.g., http://localhost:4096)",
        "start opencode tui",
        "path to start opencode in",
        "start opencode server and open web interface",
        "fetch and checkout a GitHub PR branch, then run opencode",
        "run opencode with a message",
    ]:
        add(f'describe: "{text}"',
            f'describe:"{text}"',
            'describe:"%s"' % text.replace("opencode", brand))

    # --- banners and dialogs -------------------------------------------------
    add("uninstall banner", '_D("Uninstall OpenCode")', '_D("Uninstall Rosetta")')
    add("uninstall farewell",
        'T.success("Thank you for using OpenCode!")',
        'T.success("Thank you for using Rosetta!")')
    add("terminal title", 'setTerminalTitle("OpenCode")', 'setTerminalTitle("Rosetta")')
    add("mini splash wordmark", 'A(n,p,1,"OpenCode",a', 'A(n,p,1,"Rosetta",a')
    add("mini resume hint",
        "`opencode --mini -s ${c.session_id}`", "`rosetta --mini -s ${c.session_id}`")
    add("tui resume hint",
        r'\x1B[1mopencode -s ${U.sessionID}\x1B[0m`',
        r'\x1B[1mrosetta -s ${U.sessionID}\x1B[0m`')
    add("crash screen title", 'K("opencode crashed")', 'K("rosetta crashed")')
    add("crash screen version", 'nU=K("opencode ")', 'nU=K("rosetta ")')
    add("crash report body",
        '"Reported automatically from the opencode crash screen.',
        '"Reported automatically from the rosetta crash screen.')
    add("permission dialog (single)",
        '" until OpenCode is restarted."', '" until Rosetta is restarted."')
    add("permission dialog (patterns)",
        '"This will allow the following patterns until OpenCode is restarted"',
        '"This will allow the following patterns until Rosetta is restarted"')
    add("permission dialog (patterns, mini)",
        '"This will allow the following patterns until OpenCode is restarted."',
        '"This will allow the following patterns until Rosetta is restarted."')
    add("permission dialog (single, mini)",
        "`This will allow ${f.permission} until OpenCode is restarted.`",
        "`This will allow ${f.permission} until Rosetta is restarted.`")
    add("permission reject hint",
        'K("Tell OpenCode what to do differently")',
        'K("Tell Rosetta what to do differently")')
    add("/exit description", 'description:"close OpenCode"', 'description:"close Rosetta"')
    add("sound pack name", 'name:"OpenCode Default"', 'name:"Rosetta Default"')

    # --- errors and hints ----------------------------------------------------
    add("mcp auth hint",
        '"Needs authentication (run: opencode mcp auth "',
        '"Needs authentication (run: rosetta mcp auth "')
    add("model-not-found hint",
        '"Try: `opencode models` to list available models"',
        '"Try: `rosetta models` to list available models"')
    add("mcp auth note",
        'Note, opencode does not support MCP authentication yet.`',
        'Note, rosetta does not support MCP authentication yet.`')
    add("remote config auth hint",
        "`Run \\`opencode auth login ${q}\\` to re-authenticate.`",
        "`Run \\`rosetta auth login ${q}\\` to re-authenticate.`")

    # --- home-screen tips ----------------------------------------------------
    for text in [
        "Use {highlight}opencode run{/highlight} for non-interactive scripting",
        "Use {highlight}opencode --continue{/highlight} to resume the last session",
        "Use {highlight}opencode run -f file.ts{/highlight} to attach files via CLI",
        "Run {highlight}opencode serve{/highlight} for headless API access to OpenCode",
        "Use {highlight}opencode run --attach{/highlight} to connect to a running server",
        "Run {highlight}opencode upgrade{/highlight} to update to the latest version",
        "Run {highlight}opencode auth list{/highlight} to see all configured providers",
        "Run {highlight}opencode agent create{/highlight} for guided agent creation",
        "Run {highlight}opencode github install{/highlight} to set up the GitHub workflow",
        "Run {highlight}opencode debug config{/highlight} to troubleshoot configuration",
        "Create a plugin to prevent OpenCode from reading sensitive files",
        "OpenCode includes free models so you can start immediately.",
    ]:
        add(f"tip: {text[:44]}...",
            f'"{text}"',
            '"%s"' % text.replace("opencode", brand).replace("OpenCode", BRAND))

    return p


# --------------------------------------------------------------------- plumbing

def find_binary(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            sys.exit(f"no such file: {path}")
        return path.resolve()
    found = shutil.which("opencode")
    if not found:
        sys.exit(
            "opencode is not on PATH. Install it first:\n"
            "  brew install anomalyco/tap/opencode\n"
            "  npm install -g opencode-ai"
        )
    return Path(found).resolve()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def opencode_version(binary: Path) -> str:
    try:
        out = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=60)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def resign(binary: Path) -> None:
    """A modified Mach-O needs its ad-hoc signature refreshed or macOS kills it."""
    if platform.system() != "Darwin":
        return
    if not shutil.which("codesign"):
        print("  warning: codesign not found; the binary may be refused by macOS")
        return
    subprocess.run(["codesign", "--force", "--sign", "-", str(binary)],
                   check=True, capture_output=True)


def validate(patches) -> None:
    for name, old, new, _required in patches:
        if len(new) > len(old):
            sys.exit(f"patch {name!r} grows the binary ({len(old)} -> {len(new)}); refusing")
        if old[-1:] != new[-1:]:
            sys.exit(f"patch {name!r} changes its trailing delimiter; padding would be unsafe")


def apply_patches(data: bytearray, patches) -> tuple[list[str], list[str]]:
    applied, missing = [], []
    for name, old, new, required in patches:
        count = data.count(old)
        if count == 0:
            (missing).append(name)
            if required:
                sys.exit(
                    f"required patch {name!r} did not match. This build of opencode is not the\n"
                    f"one this script was written against. Nothing was written."
                )
            continue
        padded = new + b" " * (len(old) - len(new))
        data[:] = data.replace(old, padded)
        applied.append(f"{name} ({count}x)" if count > 1 else name)
    return applied, missing


def default_bin_dir(binary: Path) -> Path:
    """~/.local/bin if it is on PATH, else next to the opencode launcher."""
    path_dirs = [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    local = Path.home() / ".local" / "bin"
    if local in path_dirs:
        return local
    return Path(shutil.which("opencode") or binary).parent


def is_source_launcher(path: Path) -> bool:
    launcher = REPO_ROOT / "bin" / "rosetta"
    if path.is_symlink():
        return path.resolve() == launcher.resolve()
    if not path.is_file():
        return False
    try:
        return b"from rosetta.cli import main" in path.read_bytes()[:4096]
    except OSError:
        return False


def install_command(binary: Path, bin_dir: Path | None) -> Path:
    target = (bin_dir or default_bin_dir(binary)).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    link = target / brand
    launcher = REPO_ROOT / "bin" / "rosetta"
    if not launcher.is_file():
        sys.exit(f"source-aware launcher is missing: {launcher}")
    if link.is_symlink() or link.exists():
        if is_source_launcher(link):
            return link
        # Migrate the older branding script's direct binary alias. That alias
        # looked right but bypassed the MCP, agents, instructions and commands.
        if link.is_symlink() and link.resolve() == binary.resolve():
            link.unlink()
        elif link.is_file():
            sys.exit(f"refusing to overwrite existing command: {link}")
        else:
            sys.exit(f"refusing to overwrite existing command: {link}")
    link.symlink_to(launcher)
    return link


def state_path(binary: Path) -> Path:
    return binary.with_name(binary.name + STATE_SUFFIX)


def read_state(binary: Path) -> dict:
    path = state_path(binary)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def cmd_check(binary: Path) -> int:
    state = read_state(binary)
    backup = binary.with_name(binary.name + BACKUP_SUFFIX)
    print(f"binary   {binary}")
    print(f"version  {opencode_version(binary)}")
    print(f"backup   {backup if backup.is_file() else 'none'}")
    if not state:
        print("branding not applied")
        return 1
    digest = sha256(binary)
    if digest == state.get("patched_sha256"):
        print(f"branding applied ({state.get('patch_count')} patches, "
              f"opencode {state.get('version')})")
        status = 0
    else:
        print("branding stale: the binary changed since it was patched "
              "(an upgrade?). Re-run this script.")
        status = 1
    link = Path(shutil.which(brand) or "")
    on_path = link.name == brand and is_source_launcher(link)
    print(f"command  {link if on_path else 'not installed on PATH'}")
    return status


def cmd_revert(binary: Path) -> int:
    backup = binary.with_name(binary.name + BACKUP_SUFFIX)
    if not backup.is_file():
        sys.exit(f"no backup at {backup}; nothing to revert to")
    shutil.copy2(backup, binary)
    resign(binary)
    state_path(binary).unlink(missing_ok=True)
    link = Path(shutil.which(brand) or "")
    if link.name == brand and link.is_symlink() and (
        link.resolve() == binary or is_source_launcher(link)
    ):
        link.unlink()
        print(f"removed {link}")
    print(f"restored {binary} from {backup.name}")
    return 0


def cmd_apply(binary: Path, bin_dir: Path | None, want_command: bool) -> int:
    patches = build_patches()
    validate(patches)

    backup = binary.with_name(binary.name + BACKUP_SUFFIX)
    if not backup.is_file():
        print(f"backing up -> {backup.name}")
        shutil.copy2(binary, backup)
    original = sha256(backup)

    data = bytearray(backup.read_bytes())
    applied, missing = apply_patches(data, patches)

    tmp = binary.with_name(binary.name + ".rosetta-tmp")
    tmp.write_bytes(data)
    shutil.copymode(binary, tmp)
    os.replace(tmp, binary)
    resign(binary)

    probe = subprocess.run([str(binary), "--version"], capture_output=True, text=True)
    if probe.returncode != 0:
        shutil.copy2(backup, binary)
        resign(binary)
        sys.exit(f"patched binary failed to run; reverted.\n{probe.stderr.strip()}")

    state_path(binary).write_text(json.dumps({
        "brand": BRAND,
        "version": probe.stdout.strip(),
        "patch_count": len(applied),
        "original_sha256": original,
        "patched_sha256": sha256(binary),
    }, indent=2) + "\n")

    print(f"patched {len(applied)} branding sites in {binary}")
    for name in applied:
        print(f"  + {name}")
    for name in missing:
        print(f"  - not found (skipped): {name}")

    if want_command:
        link = install_command(binary, bin_dir)
        print(f"installed command: {link}")
        if not shutil.which(brand):
            print(f"  note: {link.parent} is not on PATH")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Rebrand the installed OpenCode binary as Rosetta.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--binary", help="path to the opencode executable (default: from PATH)")
    ap.add_argument("--bin-dir", help="directory to install the `rosetta` command into")
    ap.add_argument("--no-command", action="store_true", help="patch only")
    ap.add_argument("--check", action="store_true", help="report state, change nothing")
    ap.add_argument("--revert", action="store_true", help="restore the original binary")
    ap.add_argument("--print-logo", action="store_true", help="preview the wordmark")
    args = ap.parse_args()

    if args.print_logo:
        print_logo(color=sys.stdout.isatty() and not os.environ.get("NO_COLOR"))
        return 0

    binary = find_binary(args.binary)
    if args.check:
        return cmd_check(binary)
    if args.revert:
        return cmd_revert(binary)
    bin_dir = Path(args.bin_dir) if args.bin_dir else None
    return cmd_apply(binary, bin_dir, not args.no_command)


if __name__ == "__main__":
    sys.exit(main())
