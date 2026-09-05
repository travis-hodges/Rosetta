# Rosetta website

`index.html` is the complete product website. Open it directly, offline, with no install
or build step. All artwork, styles, and behavior are inline. The original PLUMB master
specification and `docs/PROJECT.md` inform the product narrative; the current product
brand is Rosetta. The agent orchestrator is build infrastructure and is not marketed here.

## Views and motion

- `#/` — product story, illustrative verification walkthrough, method, benchmark, deployment.
- `#/download` — CLI and desktop availability by platform, reference runtime commands, FAQ.
- `#/account` — account-service entry point with an explicit unavailable state.
- `#/demo`, `#/how-it-works`, `#/benchmark`, `#/deployment`, `#/methodology` — home sections.

The header and footer share navigation. Tabs implement arrow, Home, and End keys; the
mobile menu closes on navigation or Escape. Focus moves to main content after navigation.
Motion respects the OS reduced-motion preference and the footer pause control. Canvas
rendering pauses offscreen, in background tabs, and outside the home view. SVG is the
fallback for unavailable canvas. There are no external fonts, analytics, or runtime CDNs.

## Local use and deployment

Node 22+ is needed only for the optional server, build, and tests:

```sh
node scripts/serve.mjs             # http://127.0.0.1:5173
node --test tests/web.test.mjs
node scripts/build.mjs             # copies web/index.html to dist/index.html
node scripts/serve.mjs --dist
```

`npm run dev`, `npm test`, `npm run build`, and `npm run preview` are aliases. No `npm
install` is necessary. Vercel uses `framework: null`, the build command, and `dist/`.
The root `index.html` is only an entry link/redirect for opening the source checkout.
Hash routes work when served, opened from disk, or refreshed directly.

## Connect accounts and actual downloads

Edit the `rosetta-config` JSON block inside `web/index.html`. These values are public
configuration, never credentials. `authUrl` is the HTTPS entry point of an actual account
service that owns authentication, sessions, and any callback handling. It is not an OAuth
client-side simulation. The site never collects a password or invents a session.

A downloadable release entry is `{ "url": "<HTTPS artifact URL>", "version": "<version>" }`.
Place it under `downloads.cli` or `downloads.gui` at one of these keys:

| Key | Platform label |
| --- | --- |
| `mac-arm` | macOS · Apple Silicon |
| `mac-intel` | macOS · Intel |
| `linux` | Linux · x86_64 |
| `windows` | Windows · x86_64 |

Null entries remain visibly unavailable. Merely appearing in the selector is not a support
claim. Only configure real, verified artifacts for supported platforms. HTTPS is required;
URLs containing embedded credentials are rejected. The UI does not validate binary
signatures, manufacture installers, or implement account infrastructure.

## Benchmark contract

Publish the actual producer's output at repository-root `results/summary.json`. The build
copies it to `dist/results/summary.json`; the dev server serves it directly. Missing files
return 404 and show the pending state. On `file://`, users can load a local JSON report via
the file picker; no upload occurs. Locally supplied reports are clearly distinguished from
published reports. A passing schema check validates format, not scientific provenance.

The explicit version 1 schema replaces the earlier underspecified percentage/fraction
example in `public/results/README.md`. The producer must emit:

| Field | Requirement |
| --- | --- |
| `schema_version` | `1` |
| `generated_at` | Parseable timestamp, preferably ISO 8601 |
| `task_count` | Positive integer |
| `split_hash` | SHA-256 of the locked routine split, 64 hexadecimal characters |
| `conditions` | Two or three unique conditions; `baseline` and `scaffolded` required |
| `conditions[].id` | `baseline`, `scaffolded`, optionally `tuned_scaffolded` |
| `conditions[].pass_at_1` | Number between 0 and 1 inclusive |
| `conditions[].false_confidence_rate` | Number between 0 and 1 inclusive |

There are no performance defaults. Percent values such as `31`, numeric strings, duplicate
conditions, missing provenance, malformed JSON, and nonfinite rates are rejected. Reports
are limited to 1 MB. User-controlled data is rendered with `textContent`, never HTML. The
published fetch has a five-second timeout. A local report selected during that fetch takes
precedence. Synthetic fixtures live only in tests and must never be published as results.

The mutation-proxy limitation and input-coverage boundary are visible on the page. The
side-by-side is explicitly a synthetic illustration, not an actual runtime execution or
recording. The reference container commands prepare WorldVistA; they are not a Rosetta
installation command. No unverified pip package name is suggested.

## Verification and remaining integration work

The Node suite checks offline structure, script syntax, route/ID relationships, URL and
release validation, report edge cases, the build output, HTTP serving, and missing-report
behavior. Existing orchestration tests remain separate and untouched.

Before release, render at desktop and mobile widths in a browser. Check horizontal
wrapping, focus visibility, the animated artwork, menu, tabs, walkthrough, clipboard,
report upload, and configured/unconfigured download states. This workspace had no browser
binary, and downloading Playwright Chromium was denied by the network policy; rendered
browser QA could not be performed here. Static checks are not a substitute for that review.

Actual account service, packaged CLI/desktop artifacts, benchmark data, and an execution
recording were not present in the repository. Their unavailable/illustrative states are
intentional. Additional language support and government accreditation are roadmap items,
not deployed capabilities or claims of compliance.
