# Distribution — how Rosetta gets onto someone else's machine

Companion to [`PROJECT.md`](PROJECT.md) (what Rosetta is) and
[`WORKFLOWS.md`](WORKFLOWS.md) (what it does once installed). This file is the
**procedure**, not the implementation. Nothing here is built yet.

---

## 1. What actually ships

"Download Rosetta" is three payloads with three different owners. Conflating them is
the main way this goes wrong.

| Payload | Size | Owner | Ships in our artifact |
|---|---|---|---|
| **The tool** — `rosetta/`, `bin/`, `scripts/` | ~900 KB | us | yes |
| **The data** — `data/` (506 files: 2.4 MB routines, split lock, task sets, FileMan dict) | 5.5 MB | VA / us | yes — see §3.2 |
| **The runtime** — `worldvista/vehu` container + YottaDB | multi-GB | WorldVistA | **never** |

The tool has **zero Python dependencies** and needs only Python 3.11+. That is the
single most useful fact about distributing it: there is no dependency resolution, no
compiled extension, no platform matrix. A tarball and a launcher is the whole job.

The runtime is provisioned separately by [`scripts/bootstrap.sh`](../scripts/bootstrap.sh),
which pulls the container from Docker Hub. An install must **succeed without it** and
say so plainly — `rosetta demo` and `rosetta doctor` are designed to work with no
container and no network, and that is what a first-run experience is built on.

## 2. What is built

Decided: **Apache-2.0**, GitHub as the source of the artifact, a `curl | sh` front
door, one install line on the landing page and full detail at `/download`, and the
default Vercel domain for now (nothing hard-codes a host).

Built and verified:

| | |
|---|---|
| `LICENSE`, `NOTICE` | Apache-2.0, with VA VistA provenance carved out — see §3.1 |
| `rosetta.__version__` | single source of truth; `pyproject.toml` reads it; `rosetta --version` works |
| `scripts/make-tarball.sh` | deterministic release tarball + `SHA256SUMS` (2.0 MB, 605 entries) |
| `public/install.sh` | POSIX `sh` installer: verify-then-unpack, `--uninstall`, mirror support |
| `public/releases.json` | the manifest the page reads; ships in a pending state |
| `download.html`, `src/download.js` | the `/download` page, version and checksum rendered from the manifest |
| `index.html` `#start` | one install line replacing four command rows, two of which were dead |
| `scripts/build.mjs` | publishes every top-level page; asset check per page |
| `scripts/serve.mjs` | clean URLs and `text/plain` for `install.sh`, matching Vercel |
| `vercel.json` | `cleanUrls`, plus headers for `install.sh` and `releases.json` |
| `.github/workflows/release.yml` | tag-triggered; the whole §4 procedure |
| `tests/web.test.mjs` | 12/12, generalized over pages, guards the install artifacts |
| `tests/test_cli.py` | new `PublishedSurfaceTests` — the site cannot advertise a dead command |

Verified end to end, not just written: the tarball builds byte-identically twice, the
installer downloads and checksum-verifies it, refuses a corrupted one, refuses a
launcher it did not create, is re-runnable, uninstalls cleanly, and the installed copy
runs `rosetta demo` **offline with no repo and no container**. The download page was
checked in a browser in both its pending and published states.

Still gated on you: the repository is **private**, so no GitHub URL resolves for anyone
else yet. See §7.

Not doing now: PyPI, Homebrew tap, a separately branded air-gap bundle. Recorded in §8
with what would trigger reconsidering.

## 3. Prerequisites — must land before any download exists

These are ordered. Each one is a thing a downloader hits immediately if it's missing.

### 3.0 Repair the verification you are about to rely on

**Status: partly fixed. The rest needs a product decision.**

Fixed:

- `tests/test_install.py` referenced `cli.ROOT`, renamed to `cli.REPO_ROOT` in `6d3109e`.
  All 11 tests died in `setUp`. Renamed. This recovered the three installer-contract
  tests §5 depends on — `test_reinstall_does_not_overwrite_existing_launcher`,
  `test_install_requires_explicit_destination`, `test_missing_checkout_fails_clearly` —
  which now pass.
- `tests/web.test.mjs` was 3/11 red: `/favicon.svg` resolved without `build.mjs`'s
  `public/` fallback; `www.gao.gov` and `department.va.gov` citations were undeclared;
  and the benchmark-claims guard fired on `max-width:100%` inside `<noscript><style>`.
  All three fixed, plus the origin check now reports every offender at once instead of
  aborting on the first. **11/11 green.**
- The claims guard now reads visible text with `<style>`, `<script>` and attributes
  stripped, and carries the §4 `sha256` assertion. Checked against six cases so it is
  not vacuous: it still flags `94%` and `7.5 %` in prose and a 64-hex literal, and no
  longer flags CSS.
- Strays removed: `index 2.html`, `scripts/build 2.mjs`, `public/og 2.png`,
  `tests/web.test 2.mjs`.

Outstanding — **29 Python failures in three groups**, none mechanical:

**A. The removed CLI contract (25).** `6d3109e` deleted the `code`, `eval` and `models`
commands and replaced the machine-readable `doctor` with human-readable text.
`tests/test_install.py` and `tests/test_rescue.py` both still assert the old contract —
`doctor` emitting JSON with `ok`, `mcp_tools: 8` and `source_checkout`, plus
`code --prompt --timeout` and `eval --baseline --candidate --cases --out`. Either
`doctor --json` comes back (two test files and the MCP surface all want it) or these
tests are rewritten against `status`/`verify`/`edit`/`model`. That is a product call.

**This group has already leaked to the website.** `index.html` ships copy-buttons for
`python3 -m rosetta code` and `python3 -m rosetta eval --help`; both now exit 2 with an
argparse error. A visitor who follows the front page gets a broken command today. §6
replaces those rows anyway, but it is a live bug, not a cosmetic one.

**B. The GUI is genuinely off-brand (1).** `test_the_gui_uses_the_site_palette` is
**correct and the drift is real** — not a stale test. `rosetta/gui/static/app.css` is
still the dark/lemon palette (`--black:#020202`, `--lemon:#f2ff66`, `--line:#242424`)
while `src/styles.css` was rebranded to cream/charcoal/acid (`--paper:#eeeee7`,
`--ink:#242720`, `--acid:#ddf95c`). Its docstring says it exists so "the next change is
a failure, not a surprise" — it worked. Fixing it is a visual redesign of the GUI from
dark to cream, not a token swap, so it is a design decision.

**C. A lost safety property, which is the real find (part of A's count).** Retargeting
the malformed-case test from `eval` to `verify` shows the validation did not survive the
rewrite. `rosetta verify` with `--cases '[{"routine":123}]'` — a non-string routine name
— **executes and reports a divergence** instead of refusing the input. Exit code on bad
input also changed from 2 to 1. `AGENTS.md` requires failing loudly rather than
swallowing errors in the verifier; malformed input silently executing is the opposite,
and the test that guarded it has been dead since `6d3109e`. Fixing it touches verifier
input validation, so it is deliberately not done here.

### 3.1 `LICENSE` + `NOTICE`

`LICENSE` is the stock Apache-2.0 text. `NOTICE` is the part that needs care:

`data/routines/*.m` are **verbatim VA VistA routines** — e.g. `GMRVPCE0.m` still
carries its `;;5.0;GEN. MED. REC. - VITALS;**8**;Oct 31, 2002` header. These are
U.S. Government work obtained via FOIA release. They are **not ours to relicense**.
`NOTICE` must state their provenance and that the repository license covers Rosetta's
own code, not the redistributed VistA source. Same for the derived artifacts in
`data/tasks/` and `data/fileman/dd.json.gz`.

Add a `license` field to `pyproject.toml` at the same time.

### 3.2 A version, in exactly one place

There is no `__version__` anywhere today and no git tags. `pyproject.toml` says
`0.1.0`; `package.json` says `0.2.0` for the *website*, which is a separate thing and
should stay separate.

Procedure: `rosetta/__init__.py` holds `__version__` as the single source of truth,
and `pyproject.toml` reads it:

```toml
dynamic = ["version"]

[tool.setuptools.dynamic]
version = {attr = "rosetta.__version__"}
```

This is the ordering that works for both an installed package and a bare source
checkout — `importlib.metadata` alone fails in a checkout, which is exactly how the
installer leaves things.

Then: `rosetta --version` prints it, and `rosetta doctor` prints it plus how it was
installed. Without that second part, a bug report cannot be traced to a channel.

### 3.3 Decide the data question

`[tool.setuptools.package-data]` currently ships only `rosetta/core/m/*.m`. `data/` is
outside the `rosetta*` package tree, so it is in no build artifact at all — meaning a
packaged install produces a CLI whose `doctor` reports `split lock missing` and whose
`bench` has nothing to run.

**Recommendation: ship all of `data/` and `results/canned/`.** 5.5 MB is nothing, and
the alternative is a second download step on first run, which breaks the air-gap story
for the sake of saving five megabytes. `results/canned/` is already committed
deliberately so the demo survives a clone — the same reasoning applies to a tarball.

For the tarball this is free (it's a source tree). It only becomes a packaging question
if PyPI is revisited.

### 3.4 Resolve the `install.sh` name collision

[`scripts/install.sh`](../scripts/install.sh) already exists and does something
different: it creates a launcher pointing at an *existing* checkout, with no network
and no shell edits. The web installer is a different job (fetch, verify, unpack, link).

Procedure: leave `scripts/install.sh` alone — docs reference it and renaming it breaks
them. The new one lives at `public/install.sh` and is served at `/install.sh` because
`build.mjs` flattens `public/` onto the site root. After unpacking, the web installer
**delegates to `scripts/install.sh --bin-dir`** rather than reimplementing launcher
creation. One code path for the thing that has to be right.

## 4. Release procedure

Per release, in order:

1. **Bump** `rosetta.__version__`. Update `docs/` if the surface changed.
2. **Verify locally** — `python -m compileall -q rosetta tests`,
   `python -m unittest discover -s tests`, `node --test tests/web.test.mjs`,
   `node scripts/build.mjs`.
3. **Tag** `v<version>`, annotated, and push the tag.
4. **`.github/workflows/release.yml` fires on `v*`** and:
   - re-runs the full check suite — a tag must not be able to publish a red tree,
     which requires §3.0 first, because today it always would;
   - asserts the tag matches `rosetta.__version__`, failing loudly on mismatch;
   - builds `rosetta-<version>.tar.gz` from the tag, containing `rosetta/`, `bin/`,
     `scripts/`, `data/`, `results/canned/`, `docs/`, `README.md`, `LICENSE`, `NOTICE`,
     and nothing else — no `.git`, no `node_modules`, no `dist`, no `results/*` beyond
     canned, no `data/models.json`;
   - writes `SHA256SUMS`;
   - creates the GitHub Release with both files attached;
   - writes `public/releases.json` — version, tag, tarball URL, sha256 — and commits it
     so the next site deploy picks it up.

   Needs `permissions: contents: write`; the existing `ci.yml` is read-only and stays
   that way.
5. **Update the pinned version and checksum in `public/install.sh`** from
   `releases.json`. This is the one manual-looking step, and it should be generated by
   the workflow, not typed.
6. **Verify the published path end to end** on a clean machine or container: run the
   one-liner, then `rosetta --version`, `rosetta doctor`, `rosetta demo`. The demo is
   the check that matters — it proves the artifact is complete offline.

### The `releases.json` rule

The website must **never hand-author a version string or a checksum**, for the same
reason [`rosetta/bench/report.py`](../rosetta/bench/report.py) owns benchmark numbers
and `tests/web.test.mjs` forbids hand-written percentages: a stale hash on a download
page is worse than no hash, because it teaches people that verification fails normally.
`/download` reads `public/releases.json` at runtime and shows a pending state if it is
absent — mirroring how the page already handles missing `results/summary.json`.

Extend `tests/web.test.mjs` with an assertion that no `sha256`-shaped literal appears
in the page — over stripped text content, not raw HTML, for the reason in §3.0.

## 5. Installer contract

What `public/install.sh` must do:

- **POSIX `sh`**, not bash. It runs on whatever the visitor has.
- **Fetch, then verify, then unpack** — in that order, into a temp dir, and only move
  into place after the sha256 matches its pinned value. Abort loudly on mismatch with
  the expected and actual hashes both printed.
- **Check Python 3.11+ first** and exit with the version it found before downloading
  anything. Honour `ROSETTA_PYTHON`, as `scripts/install.sh` already does.
- **Install to** `~/.local/share/rosetta/<version>/` with the launcher at
  `~/.local/bin/rosetta`, both overridable (`ROSETTA_HOME`, `--bin-dir`).
- **Never edit shell rc files.** Print the `PATH` line and let the user run it. This is
  the existing convention in `scripts/install.sh` and it is the right one.
- **Be re-runnable.** Installing over an existing version replaces the versioned
  directory and relinks; it never silently clobbers a launcher it did not create.
- **Print the next command** — `rosetta doctor` — because every other command in this
  repo does.
- **Support `--uninstall`.** Removing it must be one command, and versioned install
  directories make that clean.
- **Print nothing sensitive.** No env dumps, no tokens, no absolute paths beyond the
  install target.

What it must not do: `sudo`, write outside `$HOME` without an explicit flag, pull the
container, or touch Docker.

Trust note worth stating on `/download` rather than hiding: piping a remote script to a
shell is trust-on-first-use — the pinned checksum protects the *payload*, not the
installer. So `/download` documents the two-step form as an equal option:

```sh
curl -fsSLO https://<domain>/install.sh
less install.sh && sh install.sh
```

For a project whose pitch is "we never see your code," showing that path is an asset,
not a hedge.

## 6. Website procedure

**Landing page** (`index.html`, `#start` section): the existing four `python3 -m
rosetta ...` command rows get replaced by one install line with the same copy-button
pattern, plus a link to `/download`. The current rows tell a visitor to run commands
from a checkout they do not have — that ordering is backwards once an installer exists.

**`/download`**: a new page carrying the version and checksum (from `releases.json`),
the two-step verifiable form, Python/platform requirements, the "runtime is separate"
explanation pointing at `bootstrap.sh`, and uninstall.

Build and test changes this forces — small but real:

- `scripts/build.mjs` copies only `index.html`. It must copy every top-level HTML page
  and run its asset check over each — and the strays in §3.0 must be gone first, or
  `index 2.html` gets published.
- `tests/web.test.mjs` reads `index.html` at module scope. Generalize it to iterate
  pages so `/download` inherits the same guarantees. Fix the §3.0 failures first;
  generalizing a red test multiplies the red.
- The external-origin allowlist in that test already lists `github.com`, so downloads
  need no addition — but it still needs `www.gao.gov` per §3.0.
- Vercel serves `/install.sh` as a static file; confirm the `Content-Type` and that
  `vercel.json`'s `X-Content-Type-Options: nosniff` doesn't interfere with `curl`.
  It shouldn't — `curl` ignores it — but verify rather than assume.

## 7. Open items

**1. The repository is private.** This is the only thing standing between the built
plumbing and a working download. `gh repo view` reports `PRIVATE`, so
`github.com/travis-hodges/Rosetta/releases/download/...` returns 404 for everyone but
you, and the installer's own error message says as much rather than failing obscurely.

Making it public also publishes `docs/PROJECT.md` — market analysis, competitor table,
the pitch — and the full git history. That was flagged and the call was made anyway;
noting it here so the decision is on the record rather than a surprise later.

To publish:

```bash
gh repo edit travis-hodges/Rosetta --visibility public
```

**2. No release exists.** There are no tags on the remote. Once the repo is public:

```bash
git tag -a v0.1.0 -m "Rosetta 0.1.0" && git push origin v0.1.0
```

That fires `release.yml`, which re-runs the checks, asserts the tag matches
`rosetta.__version__`, builds and verifies the tarball, installs from it, runs the demo,
publishes the release, and commits the pinned `install.sh` plus `releases.json`. The
download page flips from pending to live on the next Vercel deploy — no hand-editing.

**3. The domain.** Everything derives the host from `location.origin`, so the default
Vercel domain works today and a custom domain needs no code change. When one is chosen,
add `og:url` and a canonical link, which neither page has.

**4. `~/.local/bin/rosetta` is taken on your machine** — by a Bun-compiled binary. The
installer correctly refuses to overwrite it, so your own first install needs
`--bin-dir` or that file removed. Worth knowing before you demo it.

## 8. Deferred, with triggers

- **PyPI.** Reconsider when someone asks to depend on Rosetta programmatically, or when
  `pipx install` shows up in a third party's instructions. Would need OIDC trusted
  publishing and the §3.3 packaging decision. Distribution name `rosetta-legacy` is
  already declared; the command stays `rosetta`.
- **Homebrew tap.** Costs a second repository and a formula bump per release. Not worth
  it below meaningful macOS demand.
- **Air-gap bundle.** The §4 tarball is *already* self-contained and offline-capable —
  `data/` and `results/canned/` are in it by §3.3. A separately branded bundle is a
  documentation and naming exercise on `/download`, not a new artifact. Revisit if an
  enclave delivery needs the container image alongside it, which is a genuinely
  different problem.
- **Signing (Sigstore / minisign).** Checksums plus HTTPS is proportionate now.
  Reconsider the moment a program office asks, because they will.
