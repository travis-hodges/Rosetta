#!/bin/sh
# Rosetta installer.
#
#   curl -fsSL https://<site>/install.sh | sh
#
# Or, if you would rather read it first -- and you should, this is a shell script
# from the internet:
#
#   curl -fsSLO https://<site>/install.sh
#   less install.sh && sh install.sh
#
# What it does: checks for Python 3.11+, downloads a release tarball from GitHub,
# verifies it against that release's SHA256SUMS, unpacks it under
# ~/.local/share/rosetta/<version>/, and creates a launcher at ~/.local/bin/rosetta.
#
# What it does not do: use sudo, edit your shell profile, install a container, or
# touch Docker. Rosetta runs its verifier inside a WorldVistA container that you
# provision separately with scripts/bootstrap.sh; `rosetta demo` and `rosetta doctor`
# work without it.
#
# Uninstall:  sh install.sh --uninstall

set -eu

REPO="travis-hodges/Rosetta"

# Written by .github/workflows/release.yml at publish time. The sentinel below is
# what lives in git, so a checkout that has never been released says so plainly
# instead of guessing at a tag that does not exist.
PINNED_VERSION="0.1.1"

BIN_DIR="${ROSETTA_BIN_DIR:-$HOME/.local/bin}"
HOME_DIR="${ROSETTA_HOME:-$HOME/.local/share/rosetta}"
VERSION=""
UNINSTALL=0

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarn:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
Usage: sh install.sh [--version X.Y.Z] [--bin-dir PATH] [--uninstall]

  --version X.Y.Z   install a specific release (default: the pinned release)
  --bin-dir PATH    where to put the launcher (default: ~/.local/bin)
  --uninstall       remove the launcher and every installed version
  -h, --help        this text

Environment:
  ROSETTA_PYTHON    interpreter to use (default: python3)
  ROSETTA_HOME      where versions are unpacked (default: ~/.local/share/rosetta)
  ROSETTA_BIN_DIR   same as --bin-dir
  ROSETTA_BASE_URL  install from a mirror holding the tarball and SHA256SUMS
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --version)   [ $# -ge 2 ] || die "--version needs a value"; VERSION="$2"; shift 2 ;;
    --version=*) VERSION="${1#*=}"; shift ;;
    --bin-dir)   [ $# -ge 2 ] || die "--bin-dir needs a value"; BIN_DIR="$2"; shift 2 ;;
    --bin-dir=*) BIN_DIR="${1#*=}"; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    *)           die "unknown argument: $1 (try --help)" ;;
  esac
done

# ------------------------------------------------------------------- uninstall

if [ "$UNINSTALL" = 1 ]; then
  removed=0
  launcher="$BIN_DIR/rosetta"
  # Only remove a launcher we recognise as ours. Someone else's `rosetta` on the
  # PATH is not ours to delete.
  if [ -f "$launcher" ] && grep -q "$HOME_DIR" "$launcher" 2>/dev/null; then
    rm -f "$launcher"; say "removed $launcher"; removed=1
  elif [ -e "$launcher" ]; then
    warn "left $launcher alone: it was not created by this installer"
  fi
  if [ -d "$HOME_DIR" ]; then
    rm -rf "$HOME_DIR"; say "removed $HOME_DIR"; removed=1
  fi
  [ "$removed" = 1 ] || say "nothing to remove"
  exit 0
fi

# ---------------------------------------------------------------- prerequisites

need() { command -v "$1" >/dev/null 2>&1 || die "$2"; }

# Python first: it is the one requirement that cannot be worked around, so failing
# on it before downloading anything respects the user's bandwidth and patience.
PYTHON="${ROSETTA_PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || die "$PYTHON not found. Rosetta needs Python 3.11 or newer."
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  die "$("$PYTHON" -c 'import sys; print("found Python " + sys.version.split()[0])'), but Rosetta needs 3.11 or newer.
      Set ROSETTA_PYTHON to a newer interpreter if you have one."
fi

if command -v curl >/dev/null 2>&1; then
  fetch() { curl -fsSL "$1" -o "$2"; }
elif command -v wget >/dev/null 2>&1; then
  fetch() { wget -qO "$2" "$1"; }
else
  die "neither curl nor wget found; cannot download"
fi

if command -v sha256sum >/dev/null 2>&1; then
  digest() { sha256sum "$1" | cut -d' ' -f1; }
elif command -v shasum >/dev/null 2>&1; then
  digest() { shasum -a 256 "$1" | cut -d' ' -f1; }
else
  die "neither sha256sum nor shasum found; cannot verify the download"
fi

need tar "tar not found; cannot unpack the release"
# scripts/install.sh -- which creates the launcher -- is a bash script, and it is
# the one copy of that logic. Reimplementing it here would mean two things to keep
# correct instead of one.
need bash "bash not found; it is needed to create the launcher"

# Decide the launcher question before spending anyone's bandwidth. A conflict here
# is fatal, and finding out after a 2 MB download and an unpack is just rude.
launcher="$BIN_DIR/rosetta"
LAUNCHER_IS_OURS=0
if [ -e "$launcher" ]; then
  if grep -q "$HOME_DIR" "$launcher" 2>/dev/null; then
    LAUNCHER_IS_OURS=1
  else
    die "$launcher already exists and was not created by this installer.
      Refusing to overwrite it. Remove it yourself, or pick another
      location with --bin-dir PATH."
  fi
fi

# --------------------------------------------------------------------- resolve

if [ -z "$VERSION" ]; then
  case "$PINNED_VERSION" in
    __ROSETTA*)
      die "this installer has no release pinned yet.
      No Rosetta release has been published, so there is nothing to download.
      Install from a source checkout instead:
          git clone https://github.com/$REPO.git
          cd Rosetta && bash scripts/install.sh --bin-dir \"$BIN_DIR\"" ;;
    *) VERSION="$PINNED_VERSION" ;;
  esac
fi
VERSION="${VERSION#v}"

TAG="v$VERSION"
TARBALL="rosetta-$VERSION.tar.gz"
# Overridable so an enclave can install from an internal mirror that holds the
# same tarball and SHA256SUMS. Verification is identical either way.
BASE="${ROSETTA_BASE_URL:-https://github.com/$REPO/releases/download/$TAG}"
TARGET="$HOME_DIR/$VERSION"

# ---------------------------------------------------------- fetch, then verify

work="$(mktemp -d "${TMPDIR:-/tmp}/rosetta-install.XXXXXX")"
# shellcheck disable=SC2064
trap "rm -rf '$work'" EXIT HUP INT TERM

say "Downloading Rosetta $VERSION"
fetch "$BASE/$TARBALL" "$work/$TARBALL" \
  || die "could not download $BASE/$TARBALL
      Check that release $TAG exists: https://github.com/$REPO/releases"
fetch "$BASE/SHA256SUMS" "$work/SHA256SUMS" \
  || die "release $TAG has no SHA256SUMS; refusing to install an unverified download"

expected="$(awk -v f="$TARBALL" '$2 == f || $2 == "*" f {print $1}' "$work/SHA256SUMS" | head -n1)"
[ -n "$expected" ] || die "SHA256SUMS does not list $TARBALL; refusing to install"
actual="$(digest "$work/$TARBALL")"
if [ "$expected" != "$actual" ]; then
  die "checksum mismatch -- refusing to install.
      expected $expected
      actual   $actual
      Do not use this download. Please report it:
      https://github.com/$REPO/issues"
fi
say "Checksum verified"

# ------------------------------------------------------------------- unpack

mkdir -p "$work/unpacked"
tar -xzf "$work/$TARBALL" -C "$work/unpacked"
# The tarball has a single top-level directory; find it rather than assuming.
root="$(find "$work/unpacked" -mindepth 1 -maxdepth 1 -type d | head -n1)"
[ -n "$root" ] && [ -f "$root/rosetta/cli.py" ] \
  || die "the downloaded archive does not look like a Rosetta release"

mkdir -p "$HOME_DIR"
# Replace the versioned directory atomically enough: move the old one aside, put
# the new one in place, then drop the old. A half-written install is never live.
if [ -d "$TARGET" ]; then
  rm -rf "$TARGET.replacing"
  mv "$TARGET" "$TARGET.replacing"
fi
mv "$root" "$TARGET"
rm -rf "$TARGET.replacing"
say "Installed to $TARGET"

# ------------------------------------------------------------------- launcher

[ "$LAUNCHER_IS_OURS" = 1 ] && rm -f "$launcher"   # ours: safe to relink
mkdir -p "$BIN_DIR"
ROSETTA_PYTHON="$PYTHON" bash "$TARGET/scripts/install.sh" --bin-dir "$BIN_DIR" >/dev/null \
  || die "could not create the launcher at $launcher"

# --------------------------------------------------------------------- report

installed="$("$launcher" --version 2>/dev/null || echo "rosetta $VERSION")"
say "$installed"

case ":$PATH:" in
  *":$BIN_DIR:"*) next="rosetta doctor" ;;
  *)
    printf '\n%s is not on your PATH. Add it:\n\n    export PATH="%s:$PATH"\n\n' "$BIN_DIR" "$BIN_DIR"
    next="$launcher doctor" ;;
esac

printf 'next  %s   # can this machine run the verifier\n' "$next"
printf '      %s     # the thesis, offline, no container needed\n' "$(printf '%s' "$next" | sed 's/doctor/demo  /')"
