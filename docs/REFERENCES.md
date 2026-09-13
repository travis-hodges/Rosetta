# Repository-provided technical references

Rosetta extends the installed OpenCode harness with four MCP tools:
`reference_sources`, `reference_search`, `reference_read`, and `reference_examples`.
Normal file inspection, editing, shell execution, permissions, conversations and
tool-event rendering remain OpenCode's. There is no language classifier or mandatory
lookup. The agent gets a short source inventory and general guidance to consult
references when syntax, semantics, APIs or runtime behavior are uncertain.

## Install and launch

Requirements: Python 3.11+, an installed OpenCode executable, and a configured model.
The reference service adds no Python or JavaScript dependencies. For model/provider
setup use OpenCode's existing configuration. Local references need no internet;
a hosted model still needs its provider connection. A fully offline agent requires
an already configured local model.

```sh
git clone https://github.com/travis-hodges/Rosetta.git
cd Rosetta
bash scripts/install.sh --bin-dir "$HOME/.local/bin"
# From any repository:
rosetta /absolute/path/to/project
# A headless run with real tool events:
rosetta code /absolute/path/to/project --format json --timeout 600 \
  --prompt 'Implement the requested change and run the project checks.'
```

The source checkout must remain at its installed location. If an existing launcher
already occupies the destination, the installer refuses to overwrite it. Use the
checkout's `./bin/rosetta` directly or choose a different bin directory.

## Attach your own manual

Put `rosetta.json` in your repository root:

```json
{
  "version": 1,
  "sources": [
    {
      "id": "internal-api",
      "title": "Internal API reference",
      "path": "docs/internal-api.md",
      "kind": "internal",
      "tags": ["widgets", "revision"],
      "file_patterns": ["*.widget", "src/*.js"],
      "priority": 10
    }
  ],
  "commands": {"test": ["python3", "check.py"]}
}
```

No Rosetta code changes are needed. `path` may name a local UTF-8 file or a directory
of `.md`, `.txt` and `.rst` files. Explicit absolute paths are also supported. A directory
source does not follow symlinks outside its registered directory. `origin` can hold a
vendor URL for provenance; retrieval never fetches that URL. `kind` and `tags` are
metadata, not language dispatch. A source with `kind: "examples"` treats all its
passages as examples; fenced code is also discoverable through `reference_examples`.

Configuration discovery walks upward from the active directory to the nearest catalog,
stopping at a `.git` boundary. Unrelated repositories get an empty catalog. Optional
`instructions` is an array of local instruction file paths. Keep technical manuals in
`sources`, not `instructions`. Commands are advertised metadata; they run only when
the agent invokes its ordinary shell tool, under the harness permissions.

Test registration without a model:

```sh
python3 -m rosetta.references --project examples/widget-sdk --sources
python3 -m rosetta.references --project examples/widget-sdk \
  --search 'revision conflict replace' --active-file sample.widget
```

`examples/widget-sdk` is a small fictional SDK reference fixture used to test
registration and retrieval. It is not a working SDK or a second runtime adapter.

## Retrieval and relevance

Documents are split at Markdown headings and bounded at 100 lines or roughly 6,000
characters. Search tokenizes text locally, weights rare query terms, boosts heading
matches, then applies source priority (0–10), metadata matches, and file relevance.
An `active_file` matching a configured pattern gets a stronger boost than merely
finding matching files in the repository. Context never creates a hit without a
lexical match. No embeddings, classifier calls or network requests are used.

Search returns at most 10 passages, each excerpt capped at 1,800 characters.
Every hit includes the source ID, document ID, original URL when declared, local path,
heading, line bounds and document SHA-256. Read more using the document ID and
`start_line`; the result includes `next_line`. A read is capped at 200 lines and
16,000 characters. The catalog reloads on each tool call so edits are immediately
visible. Current limits are 5 MB per document, 25 MB per catalog and 2,000 documents.
Missing sources and invalid configuration fail explicitly. No success result is fabricated.

The source inventory is supplied once at launch; no manual passages are injected into
ordinary prompts. For large manuals, repeated indexing is a known performance limit.
PDF, HTML and office formats need conversion to supported text before registration.
There is no public-web fallback. Technical passages are untrusted data and are explicitly
not instructions to the agent.

## Real MUMPS demo

The payment project has three interacting routines: `PAYMENT` (service), `PAYSTORE`
(persistence) and `PAYREPORT` (queries). It models synthetic accounts and integer
cents. Its catalog registers project documentation and offline copies of the official
YottaDB Programmer's Guide: functions, language features, commands and error handling.
Vendor files retain source URLs, license notices and a download/hash manifest.

Use a dedicated, quiescent verification container, never an operational database:

```sh
# From the Rosetta checkout; uses the existing WorldVistA/YottaDB setup.
export ROSETTA_CONTAINER=rosetta-reference-verify
bash scripts/bootstrap.sh
python3 -m rosetta.core.selftest
python3 examples/payments/verify.py
rosetta examples/payments
```

The initial image is large and must already be cached for offline setup. Docker must
be running. On this Mac, `colima start` starts the already-installed Docker VM.
An old verification container had unrecovered state after VM shutdown; the acceptance
run used a new dedicated container rather than altering its database.

The base project intentionally does not implement retry-safe payments. Ask Rosetta:

> Make PAYMENT.POST safe to retry by payment ID. With the same account and numerically
> equal amount, return the original receipt balance without writes, even after later
> payments. For a conflicting account or amount, return ERROR:DUPLICATE without writes.
> Preserve validation and reporting. Run cases.json and retry-cases.json with verify.py.

`cases.json` checks 10 existing behaviors. `retry-cases.json` adds four feature cases
that fail on the base project. The model chooses its own implementation; no patch is
embedded in Rosetta, the prompt, or the runner. The runner loads the actual `.m` files,
uses `rosetta.core.Runtime`, and compares stdout, errors, restarts/void status and full
captured globals against each case. Every execution is inside `clean_state()`.
It returns nonzero for failures and prints precise expected/actual values.

Additional tasks can add a statement query, a refund balance policy, or filtered totals.
Add another JSON suite and run `python3 verify.py --cases path.json`. There is no fixed
task dispatch. Tests establish finite behavioral evidence, not correctness for all inputs,
concurrent writers, external I/O or caller-local variables outside the frozen contract.

Existing VistA/FileMan CLI commands remain available (`rosetta fileman`, `rosetta db`,
`rosetta eval`, `rosetta verify`, benchmarks). Their old TUI assets remain under `.opencode`;
the generic launcher uses `.opencode/generic` so unrelated projects do not inherit the
VistA system map or domain slash commands. Prior instruction profiles are preserved in
`.opencode/legacy`. `rosetta demo` remains the explicitly recorded historical verifier
replay; it is separate from running a fresh coding task in `examples/payments`.
