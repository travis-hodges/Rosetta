# Rosetta — Master Document

**Ground truth for code nobody can read.**

## Current product direction — September 6, 2026

Rosetta is a terminal coding environment built on OpenCode, with configurable models
(including user-supplied language specialists), executable evaluations, and benchmarks.
The default product surface is the OpenCode-derived TUI: after installation, running
`rosetta` inside any project opens that project with Rosetta's tools and workflows.
The verification engine is a central component of that product. VA FOIA VistA is an
optional public corpus, not a required identity or hardcoded customer environment.
MUMPS/YottaDB is implemented; other runtime adapters and trained specialist weights are
not shipped. See [README](../README.md) and [USAGE](USAGE.md) for current commands.

The sections below preserve the original hackathon research and plan. Their schedule,
prior measurements, and model-first rejection describe that historical build sequence;
they do not override the current product direction. The frozen core contract and split
lock remain protected. Current benchmark generation disables agent tools and measures
no-feedback versus harness-feedback repair, preventing direct access to reference source.
It does not yet measure autonomous MCP tool use.

---

> **This file defines what Rosetta *is*.** Rosetta is a verification system for AI
> modification of legacy code in languages almost nobody can read — MUMPS, COBOL, JOVIAL,
> CMS-2.

Complete project specification: market context, thesis, architecture, 36-hour build plan,
benchmark methodology, pitch, and post-hackathon strategy.

Prepared for DNHacks (national security hackathon). Self-contained — a reader who has
never seen the originating conversation can execute from this document alone.

---

## Table of contents

1. [One-page summary](#1-one-page-summary)
2. [Market context](#2-market-context)
3. [The government terrain](#3-the-government-terrain)
4. [The idea](#4-the-idea)
5. [Rejected approaches](#5-rejected-approaches)
6. [Architecture](#6-architecture)
7. [The frozen contract](#7-the-frozen-contract)
8. [36-hour build plan](#8-36-hour-build-plan)
9. [Benchmark methodology](#9-benchmark-methodology)
10. [Website specification](#10-website-specification)
11. [Instructions for coding agents](#11-instructions-for-coding-agents)
12. [The pitch](#12-the-pitch)
13. [After the hackathon](#13-after-the-hackathon)
14. [Appendix: MUMPS primer](#14-appendix-mumps-primer)
15. [Sources](#15-sources)

---

## 1. One-page summary

### Problem

The U.S. government runs on code in languages almost nobody can read anymore. GAO found
8 of the 11 most critical federal legacy systems still run outdated languages, and 7
operate with known cybersecurity vulnerabilities. Both Treasury systems on that list are
COBOL and assembler. The Pentagon spends roughly two-thirds of a ~$66B annual IT budget
maintaining legacy systems. Navy tactical systems run CMS-2; Air Force avionics run
JOVIAL; the VA's health system runs MUMPS.

AI is weak at these languages and cannot easily improve: there is almost no training data,
and you cannot export code from a classified enclave to make more. The failure mode is
the dangerous one — models are **fluent and confidently wrong** about systems people
depend on. No program office can deploy that.

### Insight

**You don't need a corpus if you have an interpreter.**

Stop trying to teach the model the language. Give it a way to check its own work. Run the
real code, apply the change, run it again, diff what actually happened. Correctness
becomes machine-checkable rather than a matter of opinion.

One component — the verifier — does four jobs:

1. grades the benchmark
2. serves as a tool the agent calls while working
3. generates verified training data
4. acts as the reward function for reinforcement fine-tuning

The method is therefore corpus-agnostic. Give Rosetta a runtime for any obscure language
and it bootstraps a competent agent from zero training data — **and never needs to see
the customer's code**, which is what makes it deployable air-gapped.

### Proving ground

MUMPS / VistA. The VA's health system is the only real, public, production-scale federal
legacy estate in existence: source is FOIA-releasable, YottaDB is open source, and
WorldVistA publishes a ready-to-run container with populated globals and synthetic patient
data.

Task is **safe modification**, not translation. Translation is a decade-long program. The
VA's immediate pain is that a change request stalls for months because the people who
understood the routine retired.

### What gets built in 36 hours

| Component | What |
|---|---|
| **Verifier** | YottaDB differential execution — diff output **and** global state |
| **Mutation generator** | Manufactures benchmark tasks and training data from working code |
| **Benchmark** | 200+ auto-generated tasks, held-out split, mechanical scoring |
| **MCP tool server** | Exposes Rosetta to OpenCode or any host agent |
| **Fine-tune** | Comprehension labels from static analysis → SFT; RFT grader wrapper |
| **Demo** | Side-by-side: agent without verifier vs. with |
| **Website** | Single self-contained HTML, real numbers, install instructions |

### The number that matters

**False-confidence rate** — how often the model asserts correctness while verification
fails. It quantifies "fluent and confidently wrong," which is exactly what a program
office fears and exactly what Rosetta detects.

---

## 2. Market context

### The documented backlog

GAO-25-107795 (2025) reviewed 11 legacy IT systems across 10 agencies whose missions are
essential to government operations — health care, critical infrastructure, tax processing,
national security. Findings:

- 8 of 11 use outdated languages such as COBOL and assembler
- 4 have unsupported hardware or software
- 7 operate with known cybersecurity vulnerabilities
- Of 10 modernizations GAO flagged in 2019, only 3 were complete as of February 2025
- Both Treasury systems run COBOL and Assembly Language Code, with a dwindling pool of
  people able to support them

This is congressionally visible, repeatedly re-reported demand — the easiest kind to
underwrite.

### State and local moves faster

Unemployment insurance, Medicaid/MMIS, DMV, SNAP, courts, tax. Oregon's Employment
Department spent at least $106M replacing one COBOL-based system. DOL's FY2026 budget
requested $6M for systemwide UI IT modernization plus $25M for identity verification, and
the UI Interstate Connection Network (ICON) hub runs on mainframes losing support — it
cannot cease operation for even one day without disrupting state eligibility
determinations.

### The VA specifically

The VA awarded a commercial EHR contract in May 2018 to replace VistA. By March 2023, 5 of
150 VA medical centers (3%) had piloted the new system, with reported safety and
reliability issues at deployed sites; a House Veterans Affairs Committee bill followed in
April 2023 to terminate the contract. Meanwhile VistA — millions of lines of MUMPS —
continues to run.

### Who is already in this market

| Company | Position | Funding |
|---|---|---|
| **Code Metal** | Verifiable AI code translation. Ada/Fortran/COBOL → Rust/C++, formally verified. ~75% defense; USAF, RTX, L3Harris, Boeing. $80M OTA for WarMatrix. | $125M Series B (Feb 2026), $1.25B valuation |
| **Mechanical Orchard** | Incrementally rewrites mainframe apps by capturing and replicating system behavior from data flows. | ~$93.2M across 2 rounds (Emergence, GV) |
| **TSRI** | JANUS Studio, 32+ languages including MUMPS. Rule-based transpilation refined ~30 years. Converted 2.1M lines of OpenVistA MUMPS to Java in 2009 as a scalability demo. | — |
| **Kodesage** | On-premise AI platform for legacy modernization: Oracle Forms, PL/SQL, COBOL, PowerBuilder, RPG. | $6.6M seed (VentureFriends) |
| **Hypercubic** | Maps and rewrites legacy COBOL with AI agents. | $5.3M seed (CIV, YC) |
| **Bloop** | COBOL → readable Java. | YC pre-seed |
| **Primes** | Accenture, Deloitte, Leidos, GDIT — own the contract vehicles. | — |
| **Frontier labs** | Anthropic markets Claude Code for COBOL modernization; Claude via Bedrock GovCloud is FedRAMP High / IL5-suitable; Claude Gov exists for classified. OpenAI has government offerings. | — |

Legacy software modernization market is projected around $13.02B in 2026 rising to
~$27.30B by 2029.

### What's crowded and what isn't

**Crowded:** COBOL→Java translation as a standalone product.

**Open:**

- **Comprehension over translation.** Nobody modernizes what nobody understands.
- **Verification.** Government will not accept "the LLM says it's equivalent." Code Metal
  raised $125M essentially on this insight.
- **The surrounding estate.** A scheduler orchestrates thousands of batch jobs — commonly
  cited in the range of 2,000–5,000 with threaded dependency chains. You can translate
  every line of COBOL perfectly and still take the system down at midnight because nobody
  mapped the job network. CICS programs fuse business logic, screen handling, transaction
  management and DB2 access. Scope discovery — inventories built from source control miss
  code promoted directly to production and include dead code that inflates estimates — is
  a leading source of budget failure.
- **Obscure languages.** JOVIAL, CMS-2, HAL/S, Natural/ADABAS, mainframe assembler are
  genuinely underserved. JOVIAL and CMS-2 have compiler vendors (DDC-I, SEA) but those are
  life-support contracts, not modernization.

### A correction worth internalizing

There are **no classified programming languages.** There are obscure, unsupported, and
access-restricted ones. MIL-STD-1589C documents JOVIAL publicly. CMS-2 manuals exist.
MUMPS has an ANSI standard. The code sits in classified or ITAR environments, but the
languages are documented. **The moat is clearance and accreditation, not secrecy.**

### The low-resource language research picture

Code LLMs perform well on languages well-represented in training data and degrade on
low-resource ones. The established mitigation is **MultiPL-T**-style semi-synthetic data:
translate training data from high-resource languages down into the target, validating with
mechanically-translated unit tests and retrying until they pass. But the literature is
candid — *Enhancing Code Generation for Low-Resource Languages: No Silver Bullet* finds
gains inconsistent, with models remaining vulnerable to feature drift on sparse data, and
surveys note the absence of unified low-resource benchmarks.

**That absence is the opening.** There is no JOVIAL benchmark, no CMS-2 test suite, no
accepted acceptance criterion. Whoever builds the first credible evaluation defines how
government buys the capability.

---

## 3. The government terrain

Knowledge that changes strategy, and that most technical teams don't have.

### You don't get an ATO — you inherit one

Authority to Operate is the artifact permitting software to touch a government network.
Earning one fresh means running the NIST Risk Management Framework (SP 800-37) and
producing a System Security Plan, security assessment report, and POA&M — typically
6–18 months. No seed-stage company does this alone.

The real path is deploying onto an already-accredited platform and inheriting its
controls: **Second Front Systems' Game Warden**, or **Platform One** (Iron Bank for
hardened containers, Big Bang for the stack). GovSignals achieved IL5 in February 2026
precisely this way.

Note the distinction: **IL5 is not a FedRAMP level.** It is a DoD standard built atop
FedRAMP. A FedRAMP High provisional authorization supplemented with DoD FedRAMP+ controls
is what feeds an IL5 provisional authorization. FedRAMP High is necessary but not
sufficient. IL6 (SECRET) is a further step, and air-gapped deployment must be architected
from day one — it cannot be retrofitted.

### The one-way door breaks every flywheel

Pushing data *into* a classified enclave is straightforward. Getting anything *out*
requires a formal cross-domain solution and human review.

Consequences: no telemetry, no usage analytics, no logs, no eval results, no fine-tuned
weights returning. You ship and you go blind. No A/B testing, no rapid iteration.

**Therefore the product must be self-validating on the high side.** The customer runs the
scoring themselves, in place, and reports a number. Any business model assuming "we learn
from deployments" is broken here. This is a primary reason the verifier is the product.

### Fine-tuning in place may classify your weights

Post-train a model inside a classified enclave on classified code and the resulting
weights can inherit that classification. They do not come out. The same logic applies to
ITAR data. A per-customer model built on the high side is trapped at the customer.

Design implication: adaptation should live in retrievable, disposable artifacts rather
than weights wherever possible.

### You cannot run frontier models locally

Claude and GPT-class weights are not licensable for on-prem. Inside an enclave you run
open weights — Llama, gpt-oss, Mistral. Origin matters politically: Chinese-origin models
(Qwen, DeepSeek) are effectively non-starters for defense procurement regardless of
benchmark performance.

Practically, a quantized 70B serves on ~2× A100 80GB — a 4U server, not a datacenter. So
local deployment is feasible, and **this is the strongest argument for post-training**:
inside an enclave you cannot lean on frontier general reasoning, so lifting an open 70B on
one narrow task is load-bearing rather than merely nice.

### The hardware is commodity

"Ship self-contained compute" is an established category, not a novel idea: AWS Snowball
Edge, Azure Stack Hub, Dell and HPE "AI in a box," Nvidia DGX systems in SCIFs, AirgapAI
for local-only inference. **Don't build the box. Build what runs on a box someone already
got approved.**

Physical constraints compound: anything entering a SCIF is inspected, no wireless, TEMPEST
considerations, controlled physical media. Compute that enters a classified enclave often
never leaves, or leaves only after sanitization. The iterate-and-retrieve dev loop does
not exist.

### Clearances are a chicken-and-egg solved by subcontracting

Secret takes months; TS/SCI often 9–18+. You cannot sponsor yourself — you need a Facility
Clearance, which needs a sponsoring contract, which needs cleared people. Everyone breaks
the loop the same way: **subcontract under a prime that already holds the FCL and the
vehicle.** Expect first real work as a sub to a Leidos, GDIT, or Booz.

### ITAR is a separate axis from classification

Export-controlled technical data on weapons systems is restricted even when unclassified.
US persons only, no foreign cloud, and team composition becomes a compliance question.
Model weights trained on ITAR data may themselves be controlled.

### "Safe" means three different things

Government will ask about all three; most vendors answer only the first.

| Threat | Mitigation |
|---|---|
| **Exfiltration** — will this send our code out? | Air-gap |
| **Supply chain** — are these weights and containers trustworthy? | Iron Bank, SBOM, attestation |
| **Incorrect output** — will it confidently tell us the wrong thing about a weapons system? | **Nothing most vendors have. This is Rosetta.** |

### Contract vehicles

- **SBIR/STTR** — Phase I roughly $50–300k, Phase II ~$1–2M, and critically **Phase III
  permits an agency to sole-source you with no competition, indefinitely**, on the strength
  of earlier phases. That is the durable moat mechanism in govtech.
- **OTA** (Other Transaction Authority) — fast, used by DIU and AFWERX.
- **CSO** (Commercial Solutions Opening).
- Entry points: DIU, AFWERX, Army xTech, NSIN, In-Q-Tel.

Realistic friction: 12–24 month procurement cycles, incumbents own the vehicles, agencies
buy outcomes rather than tools, and the failure mode for a botched benefits system is
people not receiving checks — which makes buyers extremely conservative.

### The classification ladder — climb it, don't start at the top

| Tier | Example legacy code | Requirements | Realistic entry |
|---|---|---|---|
| **Public** | VistA MUMPS (FOIA-releasable) | Nothing | Today |
| **CUI** | Treasury/IRS COBOL, state UI & Medicaid | FedRAMP Mod/High or on-prem, CMMC | 6–12 mo |
| **ITAR, unclassified** | Ada/JOVIAL maintenance data | US persons, ITAR program | 3–6 mo (+ hiring constraints) |
| **SECRET / IL5–IL6** | CMS-2 Navy tactical, mission systems | FCL, cleared staff, inherited ATO | 12–24 mo |
| **TS/SCI** | IC systems | SCIF, TS/SCI staff, sponsor | 18–36 mo |

Most legacy federal code is **not** classified. Treasury's COBOL is CUI — no SCIF, no
clearances. Starting at the top rung is a category error.

---

## 4. The idea

### Simplified

The federal government runs on code in languages almost nobody can read. The problem isn't
that the code is bad — it's that nobody left can safely *change* it.

AI models are weak at these languages because there's almost no training data, and you
can't get more: the code sits in restricted environments.

So we stopped trying to teach the model the language, and instead gave it a way to check
its own work. Run the real code, make the change, run it again, compare what actually
happened. Correctness becomes machine-checkable instead of a guess.

That single component — the verifier — grades our benchmark, serves as a tool the agent
calls, generates verified training data, and acts as the reward function for fine-tuning.
We never need a dataset, and we never need to see the customer's code.

### Expanded

**We solve maintenance, not migration.**

Every vendor sells COBOL-to-Java translation. But the VA's immediate pain isn't that
VistA should be Java — it's that a change request sits for months because the three people
who understood that routine retired. Translation is a decade-long program. Safe
modification is solvable now.

**Why AI fails here today.**

Models are fluent in Python and Java because those dominate training data. MUMPS, JOVIAL,
CMS-2 and mainframe assembler are low-resource languages, and fine-tuning alone gives
inconsistent gains on sparse data.

But note what is actually missing. The *languages* are documented. What's undocumented is
the *codebase*: what this global holds, why this branch exists, which conventions the 1987
team invented and never wrote down. That knowledge is per-installation and cannot be
exported from a secure enclave — so no amount of pre-deployment training reaches it.

There is also a softer point worth holding: models may be better at these languages than
intuition suggests, because JOVIAL and CMS-2 are ALGOL descendants and structurally
familiar. MUMPS is the genuinely alien one. The *composition* of the gap — language
unfamiliarity vs. missing codebase context — is unmeasured. Nobody has instrumented it.
That determines the entire roadmap, which is another reason measurement comes first.

**The insight.**

You don't need a corpus if you have an interpreter. MUMPS runs under YottaDB (open
source). VistA source is FOIA-releasable and WorldVistA publishes a running container with
synthetic patient data. So we can execute real federal production code today, for free.

Once you can execute it, correctness stops being an opinion:

1. Start from a known state.
2. Apply the proposed change.
3. Drive the same inputs through both versions.
4. Diff the outputs **and the resulting database state**.

The intended change should appear. Nothing else should move. Anything else is a regression,
caught mechanically.

In MUMPS the globals *are* the database, so diffing post-execution state is what catches
side-effect breakage — a routine returning the right value while corrupting `^DPT` is the
actual VistA failure mode, and output-only comparison misses it entirely.

**Why it generalizes.** Give Rosetta a runtime for any obscure language and it bootstraps a
competent agent with zero pre-existing training data. That's the path from public VistA
MUMPS → CUI Treasury COBOL → ITAR-restricted Ada and JOVIAL → classified CMS-2. And
because the verifier runs air-gapped by construction, the pitch to a program office is:
*we never need to see your code.*

### How this differs from the original concept

The originating idea was: a coding platform for obscure languages built on custom
post-trained models, so engineers can understand undocumented legacy code.

| | Original | Final |
|---|---|---|
| Core bottleneck | Model lacks training data | **No ground truth exists** |
| First thing built | The post-trained model | **The verifier** |
| Training data source | Scraped / curated corpus | **Manufactured by the verifier** |
| Primary task | Understand legacy code | **Safely modify it** |
| Scope | Obscure languages broadly | **MUMPS first** (only real public federal estate) |
| Trust story | Model is more capable | **Every claim is mechanically checked** |
| What's sold | A better model | **The instrument proving the model was right** |

**What survived:** the vision, essentially intact. Bringing a competent AI collaborator to
languages where none exists is still the company, and post-training remains part of it —
with a *stronger* justification. Inside an air-gapped enclave you cannot call a frontier
model at all; you run open weights locally, where fine-tuning is load-bearing. Post-training
earns its place because of *where the code lives*, not because of data scarcity.

**What changed:** the order of operations inverted. Starting with the model hits a wall
twice — no data to train on, and no way to prove the result is good. Starting with the
verifier solves both at once.

The task also got sharper. "Understand this code" is valuable but ungradeable, which makes
it undemoable and unsellable to a risk-averse buyer. "Change this code without breaking
it" is the same underlying capability with objective pass/fail.

And scope narrowed deliberately — not because obscure languages are the wrong market, but
because JOVIAL and CMS-2 have no public corpus and no accessible compiler. VistA is the
only rung of the ladder you can stand on in 36 hours.

---

## 5. Rejected approaches

Do not re-propose without new information. Several look wrong until you know the reasoning.

**COBOL as primary language.** Not because it's crowded — because there is no public
*realistic* COBOL estate. The unsolved COBOL problems (JCL job-network mapping, CICS
untangling, scope discovery) all require a real production estate of interlocked batch
jobs, which isn't obtainable. Public COBOL is tutorial code, and tutorial code cannot
demonstrate a maintenance product. Retained only as the hour-8 fallback.

**JOVIAL / CMS-2 as proving ground.** Correct long-term market, impossible in 36 hours: no
public corpus, no accessible compiler, nothing executable. Would produce a demo with no
ground truth. These are rungs 3–4, not rung 1.

**Translation (COBOL→Java, MUMPS→anything).** Crowded and commoditizing. Also
unfalsifiable in a demo — "how do you know the output is correct?" has no good answer
without a verifier, which is the actual product.

**Forking OpenCode.** Learning a large unfamiliar agent codebase burns ~8 hours and
produces nothing differentiated. Extend via MCP instead. OpenCode is a working harness;
adopt it whole.

**Model-first / fine-tuning first.** Hits a wall twice: no data, and no proof. The verifier
solves both. Order inverted, vision retained.

**Post-training as the answer to data scarcity.** Wrong diagnosis. Language specs are
public; the *codebase* is what's undocumented, and it's non-exportable. Tools and retrieval
reach it; pre-deployment training doesn't. Post-training earns its place for air-gapped
deployment instead.

**Two containers for differential execution.** Minutes per test. Use `.dat` file snapshot,
or `TSTART`/`TROLLBACK` as fallback. At thousands of datagen cycles this is the difference
between finishing and not.

**Hand-authored benchmark tasks.** ~20+ min each on unfamiliar MUMPS plus domain knowledge
the team lacks. 30 tasks would consume ten hours. Mutation generation gives hundreds in
minutes with ground truth for free.

**Verified-modification training data.** Each example costs a full execution cycle; at
realistic pass rates you'd generate 5–10k candidates for a usable dataset. Doesn't fit.
Fine-tune on comprehension instead, where labels are deterministic and free.

**Building our own agent loop, hardware appliance, or ATO.** The box is commodity; the
barrier is accreditation, and startups inherit ATOs rather than pursuing them.

**Breadth in the pitch.** "Works for MUMPS, COBOL, JOVIAL and Ada" reads as shallow. Depth
on one system reads as real. The generalization claim belongs in the last 30 seconds.

---

## 6. Architecture

### Principle

Everything hangs off one contract: `rosetta/core/interface.py`. Define it in hour 0 and every
other workstream proceeds in parallel against stubs. **No agent changes that file without
announcing it.**

### Component map

```
                    ┌─────────────────────────┐
                    │   OpenCode (harness)    │  ← not ours, adopted whole
                    └───────────┬─────────────┘
                                │ MCP
                    ┌───────────▼─────────────┐
                    │    rosetta.tools        │  MCP server
                    │  read/parse/exec/verify │
                    └───────────┬─────────────┘
                                │
       ┌────────────────────────▼────────────────────────┐
       │                rosetta.core                     │
       │   snapshot · execute · diff · verify            │
       │        (the only thing touching YottaDB)        │
       └───┬──────────────┬──────────────┬───────────────┘
           │              │              │
 ┌─────────▼──────┐ ┌─────▼───────┐ ┌────▼──────────┐
 │ rosetta.mutate │ │rosetta.bench│ │ rosetta.train │
 │ task/data gen  │ │ run + score │ │  SFT / RFT    │
 └────────────────┘ └─────────────┘ └───────────────┘
                                │
                       ┌────────▼────────┐
                       │  rosetta.demo   │  side-by-side
                       └─────────────────┘
```

The website is fully independent — zero code dependencies, buildable in parallel from
hour 0.

### Repository layout

The product package lives at `rosetta/`.

```
Rosetta/
├── AGENTS.md                  agent operating contract (read first)
├── CLAUDE.md                  Claude-specific compliance notes
├── README.md                  pitch + quickstart
├── docs/
│   └── PROJECT.md             this file — what Rosetta is
├── rosetta/
│   ├── core/                  interface.py (FROZEN) + runtime, snapshot, diff, verify
│   ├── mutate/                mutation operators, task generation
│   ├── bench/                 select, build, run, score, report
│   ├── tools/                 MCP server
│   ├── train/                 labels, sft, grader
│   └── demo/                  side-by-side harness
├── data/
│   ├── routines/              extracted .m files
│   ├── snapshots/             YottaDB .dat captures (gitignored)
│   └── tasks/                 generated tasks + split.lock.json
├── results/                   *.jsonl traces + summary.json
├── scripts/bootstrap.sh       run FIRST
└── tests/
```

### rosetta.core — the verifier

The only module that knows about YottaDB. Everything else talks through the contract.

**Isolation strategy.** *Amended 2026-09-05 from measurement; the original plan inverted
these.* Wrap execution in a YottaDB TP frame — `TSTART ():SERIAL` / `TROLLBACK` — as the
PRIMARY mechanism. Rollback costs 22µs–1.8ms and is near-constant in transaction size
(50,000 nodes roll back in 0.15ms); TP adds no measurable execution overhead. The `.dat`
file copy is the REPAIR path only: `vehu.dat` is 3.43GB and a copy takes **21.5 seconds**,
which is fatal in a per-case loop. Use it to recover after a voided frame or a
`TRANS2BIG`, never in the hot path.

**TP guards are not optional.** Six failure modes were reproduced in the live container;
two corrupt silently rather than erroring. An unbalanced `TCOMMIT` from the code under
test commits *our* frame to the live database with no error; a bare `TROLLBACK` collapses
`$TLEVEL` to 0 and destroys the frame, after which writes go through unprotected. Guard by
asserting `$TLEVEL` immediately after the routine under test returns — a drop below entry
level makes the case **void**, never scored — and by rejecting command-position
`TSTART`/`TCOMMIT`/`TROLLBACK` in `load_routine`. This costs nothing: across all 39,612
routines in the image there are **zero** command-position TP commands, so only mutated or
model-generated source can reach these modes. Also: hold zero M locks when opening a frame
(`TPLOCK` is a hard error), set a non-recursive `$ETRAP` (an uncaught error leaves
`$TLEVEL>0` and drops into direct mode, then `NOPRINCIO` — a hang over a pipe), and treat
worker death from `HALT` as a result, not a crash.

**A dedicated, quiesced container is required.** YottaDB silently restarts a TP transaction
when another process touches its read set — reproduced live, because the stock image runs
~148 mumps processes (TaskMan submanagers, `rocto`, `%ydbgui`, HL7/RPC/VistaLink listeners)
against the same region. On restart the body re-executes, device output is **not** rolled
back and duplicates, and locals not named in `TSTART (...)` are not restored. Capture
stdout to a global inside the frame, never to a device; latch `$TRESTART` and discard the
case if it fired.

**Every Runtime needs its own routine sandbox.** *Added 2026-09-05 from a reproduced
defect.* Candidate source must go to a private directory per Runtime, not the container's
shared `/home/vehu/r`, and the stdout capture file and trigger log must be keyed by `$J`.
Sharing any of the three lets two verifiers working on the same routine *name* overwrite
each other, and the symptom is not an error — it is a confident verdict computed against
somebody else's candidate. Reproduced with four concurrent Runtimes: three of the four were
told their candidate produced output that belonged to a different candidate. For a project
whose product is "correctness is machine-checkable," this is the one defect class that
invalidates everything downstream, so `tests/test_core.py` asserts the property directly.

**Do not enable YottaDB auto-relink on the private object directory.** A trailing `*` in
`$gtmroutines` (`objdir*(srcdir)`) turns on auto-relink, which fails with
`%YDB-E-INVOBJFILE, ... due to unexpected format` on roughly the fourth relink of a
*changing* source for one routine name. That is exactly the access pattern of verifying a
stream of mutants. Small routines survive many more links than large ones, so it presents
as intermittent and environmental when it is neither — it cost 561 spurious rejections out
of 565 in a real build, and reading it as container contention sent the investigation the
wrong way for hours. Drop the `*`; ORCRC went from 280+ load failures to zero.

**One long-lived worker, not one process per case.** `docker exec` plus `mumps -run` costs
448ms per invocation, which dominates every realistic cycle. Drive a single supervised M
worker over a pipe and respawn it on death.

**Capturing `globals_out` is not free.** Journal inspection is ruled out — rolled-back work
produces no journal records at all. A full `$QUERY` walk is unusable (`^DPT` is 115,882
nodes / 2–4s at ~15µs/node). Scoped subtree walks are 2–3ms and are the default, narrowed
by the static global refs `bench/select.py` already extracts. Triggers are the fallback for
write sets that cannot be bounded statically, at 4.29µs/SET versus 0.96µs without, and
carry three constraints: `$ZTRIGGER` inside TP silently no-ops so triggers must be
installed before `TSTART`; definitions are per-(global, subscript depth); and trigger code
runs in an isolated local scope, so it must log to a global that is harvested into a driver
local *before* rollback.

**Measured throughput.** A realistic `LIST^DIC` cycle over 20 patients runs at 116/s; a
FileMan `FILE^DIE` write cycle at 18/s. `TRANS2BIG` caps a transaction at roughly 8MB /
~2,000 dirty blocks. Figures are from x86-64 emulation on an arm64 host — treat them as a
floor.

**Why global-state diffing is non-negotiable.** In MUMPS the globals *are* the database. A
routine returning the right value while corrupting `^DPT` is the real VistA failure mode,
and output-only comparison misses it. This is also our most defensible technical claim.

### rosetta.mutate — task and data generation

We break working code and ask the agent to fix it. The mutation is the ground truth.

| Operator | Example |
|---|---|
| `CMP_FLIP` | `>` → `<`, `=` → `'=` |
| `BOUNDARY` | off-by-one in `$PIECE`/`$EXTRACT` index |
| `STMT_DROP` | delete a `SET` or `DO` line |
| `VAR_SWAP` | substitute a same-scope local |
| `NAKED_REF` | corrupt a naked global reference (`^(3)`) |
| `DOLLAR_MISUSE` | `$DATA` → `$GET`, `$ORDER` direction flip |
| `POSTCOND` | drop or invert a postconditional `:` |
| `ARG_ORDER` | swap two arguments at a call site |

Difficulty is tunable by operator class. Report scores broken out by operator — it makes
the results chart far more interesting than a single number.

```python
@dataclass
class MutationTask:
    task_id: str
    routine: str
    baseline_src: str        # correct original
    mutated_src: str         # what the agent is given
    operator: str
    line_no: int
    cases: list[ExecSpec]    # input suite that distinguishes them
    difficulty: Literal["easy", "medium", "hard"]
```

A mutation is a valid task only if `verify_equivalence(baseline, mutated, cases)` returns
`equivalent=False`. **Discard equivalent mutants** — they're unkillable and would pollute
the benchmark with unscoreable tasks.

### rosetta.tools — MCP server

The portable IP. Works with any host agent, which answers "what if the labs eat the agent
layer?"

| Tool | Signature | Notes |
|---|---|---|
| `list_routines` | `(pattern?) -> [name]` | |
| `read_routine` | `(name) -> src` | |
| `parse_routine` | `(name) -> {labels, calls, globals, locals}` | static, no execution |
| `call_graph` | `(name, depth) -> graph` | |
| `resolve_global` | `(ref) -> {schema, sample_values, fileman_file}` | most valuable tool |
| `execute_routine` | `(ExecSpec) -> ExecResult` | sandboxed, snapshot-restored |
| `verify_change` | `(routine, candidate_src) -> VerifyReport` | **the money tool** |
| `run_task_cases` | `(task_id, candidate_src) -> VerifyReport` | benchmark-scoped |

`verify_change` must return *specific* divergences, not a boolean — the repair loop depends
on actionable feedback.

### rosetta.bench

- `select.py` — rank routines by shallow dependency depth (call-graph fan-out, distinct
  global references); prefer computational over UI/RPC
- `build.py` — generate tasks, validate killability, write split lock
- `run.py` — execute a model over tasks with `--tools on|off`, N attempts, capture traces
- `score.py` — pass@1, pass@k, per-operator breakdown, repair iterations,
  false-confidence rate
- `report.py` — three-bar chart + results table + `results/summary.json`

### rosetta.train

**Fine-tune on comprehension, not modification.** Comprehension labels are deterministic
and free — no execution required:

| Label source | Generated QA pair |
|---|---|
| Call graph | "What does `DPTLK` call?" / "Who calls `DPTLK`?" |
| Global refs | "Which globals does this routine write?" |
| FileMan DD | "What file does `^DPT` correspond to? What are its fields?" |
| Routine structure | "What are the entry labels and their arguments?" |
| Idiom | "Rewrite this naked reference explicitly." |

Thousands of pairs in minutes from static analysis. This buys **fluency and VistA-specific
familiarity**; tools buy **correctness** at inference time.

- `labels.py` — static extraction → QA pairs
- `sft.py` — JSONL builder for the OpenAI fine-tune API
- `grader.py` — **RFT grader wrapper: wraps `verify_equivalence` as a reward function.**
  Build this even if the RFT run is cut — a working grader interface demonstrates the claim.

---

## 7. The frozen contract

`rosetta/core/interface.py`. All streams build against this. Do not change without
announcing. *Amended 2026-09-05 after the TP viability probe: `clean_state()` is now the
primary isolation API, and `snapshot()`/`restore()` are documented as the `.dat` repair
path. The originally-specified `snapshot() -> str` / `restore(snap_id)` pairing does not
map onto TP at all — a TP frame is scoped to one M process's stack, so there is no id to
hand back and no out-of-order restore. `ExecResult` gained `restarts` and `void`, and
`VerifyReport` gained `n_void`, because a silently restarted or frame-voided case carries
no trustworthy information and must never be scored.*

The block below is the file verbatim; if they ever differ, the file wins.

```python
"""Rosetta core contract. FROZEN.

Do not modify the dataclasses or signatures in this file. Every workstream builds
against it. If a change looks necessary, stop and report rather than editing.

ISOLATION: `clean_state()` is the primary mechanism and wraps the body in a YottaDB
TP frame (TSTART/TROLLBACK). `snapshot()`/`restore()` are the .dat-copy REPAIR path,
used only when a TP frame is voided or exceeds buffer space -- they cost ~20s against
a 3.4GB region and must not appear in the per-case hot loop. See docs/PROJECT.md #6.
"""

from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Literal


@dataclass(frozen=True)
class ExecSpec:
    """One execution of one routine under YottaDB."""
    routine: str
    entry: str | None = None                                    # None = top of routine
    args: list[str] = field(default_factory=list)
    locals_in: dict[str, str] = field(default_factory=dict)
    globals_in: dict[str, str] = field(default_factory=dict)    # {"^X(1)": "abc"}
    timeout_s: float = 10.0


@dataclass(frozen=True)
class ExecResult:
    """Observable result. globals_out is half the verification signal --
    a routine can return a correct value and still corrupt the database.
    error holds the MUMPS code (M6, M7...); an error IS a divergence.

    stdout is captured to a global inside the TP frame, never to a device:
    YottaDB may silently restart a transaction, and device writes are not
    rolled back, so device-captured output duplicates across restarts.

    restarts exposes $TRESTART at end of body. Any value > 0 means the body
    ran more than once; the caller must discard and re-run the case.

    void means frame integrity was lost mid-execution -- the code under test
    collapsed $TLEVEL via an unbalanced TCOMMIT or a bare TROLLBACK. A void
    result carries NO information and must never be scored. Recover with
    restore() and respawn the worker.
    """
    stdout: str
    error: str | None
    globals_out: dict[str, str]
    duration_ms: int
    restarts: int = 0
    void: bool = False


DivergenceKind = Literal["output", "global", "error", "timeout"]


@dataclass(frozen=True)
class Divergence:
    """Must name what moved. ref is "^DPT(3,0)" for kind="global",
    or "stdout" for kind="output". The repair loop needs specificity."""
    kind: DivergenceKind
    ref: str
    expected: str
    actual: str
    case_index: int = 0


@dataclass(frozen=True)
class VerifyReport:
    equivalent: bool
    divergences: list[Divergence]
    n_cases: int
    n_diverged: int
    n_void: int = 0          # cases that produced no trustworthy result

    def summary(self) -> str: ...


# --- API ---

@contextmanager
def clean_state() -> Iterator[None]:
    """PRIMARY isolation. Open a TP frame on entry, TROLLBACK on exit.

    Rollback is 22us-1.8ms and near-constant in transaction size. Use around
    EVERY execution; an unrestored run silently poisons every subsequent test.

    Preconditions the implementation must enforce:
      - hold ZERO M locks when opening the frame (TPLOCK is a hard error)
      - assert $TLEVEL on exit; if it dropped below entry level the case is void
      - name every capture local in TSTART (...) or reconstruct capture after
        the body, since a restart does not restore unlisted locals
    Raises on TRANS2BIG (~8MB / ~2000 dirty blocks); the caller falls back to
    snapshot()/restore() for that case and marks the task heavyweight.
    """


def snapshot() -> str:
    """FALLBACK repair path. Copy the YottaDB .dat region files.

    ~20s against a 3.4GB region. This is NOT the per-case mechanism -- it exists
    to recover after a voided frame or a TRANS2BIG. Returns a snapshot id.
    """


def restore(snap_id: str) -> None:
    """Restore regions captured by snapshot(). Same ~20s cost."""


def load_routine(name: str, source: str) -> None:
    """Write source into the environment and compile. Raise with M code on failure.

    MUST reject source containing command-position TSTART, TCOMMIT or TROLLBACK.
    Real VistA never uses TP (zero occurrences across 39,612 routines), but a
    mutated or model-generated candidate that emits TCOMMIT would silently commit
    the verifier's own frame to the live database with no error raised.
    """


def execute(spec: ExecSpec) -> ExecResult:
    """Run one spec in a long-lived M worker process.

    Do NOT fork per case: `docker exec` + `mumps -run` costs ~448ms, which
    dominates every realistic cycle. One supervised worker over a pipe.
    Treat worker death (HALT in the body) as error="HALT" and respawn.
    """


def verify_equivalence(
    routine: str,
    baseline_src: str,
    candidate_src: str,
    cases: list[ExecSpec],
) -> VerifyReport:
    """Decide behavioral equivalence of two versions of one routine.

    Per case, inside clean_state(): load baseline, execute, capture, roll back;
    load candidate, execute, capture, roll back. Diff stdout, error code, and
    globals_out. Collect EVERY divergence -- do not early-return, the repair
    loop wants the full picture. Void cases are counted, never scored.

    This function is the entire project. Write it boringly and test it
    against a real routine with a real mutation.
    """
```

---

## 8. 36-hour build plan

### Before the clock starts — tonight

Three things, in order. Nothing else matters until these are done.

1. **Pull the container.** `docker pull --platform linux/amd64 worldvista/vehu`. Multi-GB,
   and on Apple Silicon it runs under emulation. Confirm it boots and you reach a MUMPS
   prompt. **If this fails tonight you have a different hackathon tomorrow.**
2. **Freeze `rosetta/core/interface.py`.** Nothing parallelizes until the contract exists.
3. **Find one VistA maintainer.** hardhats.org community, old OSEHRA mailing lists. Ask
   what it's like to change a routine. One real quote outscores four hours of code, and
   almost no other team will have it.

`scripts/bootstrap.sh` automates step 1 and — critically — hunts for the YottaDB `.dat`
region file paths. **Without those paths there is no snapshot/restore and no project.**

### Workstreams

Interfaces first, then five streams in parallel. One agent or person per stream.
**Streams must not edit each other's directories.**

| ID | Stream | Owns | Depends on |
|---|---|---|---|
| **A** | Verifier | `rosetta/core/` | contract only |
| **B** | Mutation gen | `rosetta/mutate/` | A's contract (stub OK) |
| **C** | MCP tools | `rosetta/tools/` | A's contract (stub OK) |
| **D** | Benchmark | `rosetta/bench/` | A + B |
| **E** | Website | site files | **nothing** |
| **F** | Train pipeline | `rosetta/train/` | nothing (static analysis only) |
| **G** | Demo harness | `rosetta/demo/` | C |

A is the critical path. B, C, E, F are insulated — that's the point of freezing the
contract first.

### Timeline

| Hours | Milestone | Streams |
|---|---|---|
| 0–2 | VEHU up. Contract frozen. Snapshot/restore working on `.dat` files. | A, E |
| 2–6 | `execute()` drives a real routine. `verify_equivalence()` diffs outputs **and** globals. Routine selector ranks candidates. | A, B, C, E, F |
| 6–10 | 200+ mutation tasks generated, equivalent mutants discarded, **split lock written**. Baseline run, tools off. | B, D |
| **10–12** | 🚨 **CHECKPOINT — MEASURE AND CHOOSE THE NARRATIVE** | all |
| 12–20 | MCP tools live in OpenCode. Repair loop working. Re-score with tools on. | C, D, G |
| 20–28 | Comprehension labels extracted, SFT submitted and returned, third bar scored. RFT grader wrapper written. | F, D |
| 28–32 | 3–5 hand-authored *real* tasks for the demo. Side-by-side locked. Website wired to real results. | G, E |
| 32–36 | **Rehearse. Freeze code. Rehearse again.** | all |

The last four hours are not build time and are not negotiable.

### 🚨 Hour 10–12 checkpoint

Two decisions, made from data.

**Decision 1 — is the language MUMPS?** If the verifier cannot drive at least 20 routines
by **hour 8**, abandon MUMPS. Switch to GnuCOBOL, where execution is trivial, and accept
the loss of the "real federal production estate" claim. Every other component is
language-agnostic by design — mutation operators and the contract port in about two hours.
Do not spend hour 20 fighting FileMan dependencies. Set a timer.

**Decision 2 — which narrative?** Read the baseline and pick:

- **Baseline < ~50%** → *"AI is dangerously unreliable on the code the government runs on.
  Here's the instrument that catches it."*
- **Baseline > ~70%** → *"Models are far more capable here than anyone assumed — and
  completely unverifiable, which is why no program office can deploy them. We built the
  layer that makes them deployable."*

The second story is arguably **better** for a government audience. Do not write the pitch
before the number arrives, and do not fall in love with the first framing.

### Definition of done, per stream

- **A** — `python -m rosetta.core.selftest` snapshots, mutates a known routine, detects the
  divergence with a specific global ref, restores clean. Under 5s.
- **B** — 200+ killable tasks across ≥6 operator classes, equivalent mutants discarded.
- **C** — OpenCode calls all 8 tools. `verify_change` returns actionable divergences.
- **D** — `report.py` emits three-bar chart + per-operator table. Split lock respected.
- **E** — Single HTML file, opens offline, real numbers, side-by-side GIF embedded.
- **F** — SFT JSONL from deterministic labels. Fine-tune returns a usable model ID. RFT
  grader wrapper importable and unit-tested.
- **G** — Side-by-side runs the money moment reliably, twice in a row, cold start, no network.

### Cut order

When you fall behind — and you will — cut in exactly this order:

1. RFT run (keep the grader wrapper; it's the pitch, the run is a bonus)
2. Fine-tune bar entirely (two bars is still a result)
3. Per-operator breakdown
4. Interactive judge-driven demo (keep the canned path)
5. Website sections 7 and 8

**Never cut:** the verifier, the baseline number, the side-by-side, the rehearsal.

### Failure modes to pre-empt

| Risk | Mitigation |
|---|---|
| Container amd64-only, team on Apple Silicon | Pull tonight with `--platform`; expect slow emulation |
| Routines not driveable standalone | VEHU ships populated globals — why we use it, not bare YottaDB |
| Snapshot/restore too slow for datagen | `.dat` file copy, not containers; fall back to `TSTART`/`TROLLBACK` |
| Equivalent mutants pollute benchmark | Validate killability before admitting a task |
| Train/eval leakage | Split **routines**, not tasks. Lock file before any generation. |
| Gap turns out small | Pre-written alternate narrative |
| Nobody can speak MUMPS in the demo | Assign one person 2h to become the routine explainer |
| Demo crashes live | Canned path primary, interactive gated behind it |
| Judges file this as civic tech | Open with F-35 / CMS-2, not the VA |

---

## 9. Benchmark methodology

### The task

Given a real VistA MUMPS routine containing an injected regression, produce a corrected
version whose behavior is equivalent to the pre-mutation original.

Equivalence is decided mechanically: run both against the task's input suite, compare
program output **and** resulting global state. No human judgment, no LLM judge.

### Why mutation-based

Hand-authoring realistic tasks on unfamiliar MUMPS costs ~20+ min each and requires domain
knowledge the team lacks — 30 tasks would consume ten hours of the window. Mutation
inverts the cost: break known-good code mechanically and the mutation *is* the ground
truth. Hundreds of tasks in minutes, tunable difficulty, zero MUMPS expertise required,
and the same generator produces training data.

**State the limitation openly in the pitch.** Mutation repair over-represents localized
single-line bugs and under-represents cross-routine changes and genuine feature addition.
Mitigate with 3–5 hand-authored real tasks in the demo. With a professionally skeptical
audience, naming this buys more credibility than concealing it costs.

### Validity rules

A mutant is admitted only if:

1. **It is killable** — `verify_equivalence` returns `equivalent=False`.
2. **It compiles** — a mutant that fails to load tests the wrong thing.
3. **The divergence is specific** — at least one named output or global differs. A bare
   timeout is a weak signal; flag separately.
4. **The routine is in the eval split.**

### Train/eval split — the discipline that protects every number

**Split routines, not tasks** — and split by *duplicate cluster*, not by routine.
*Amended 2026-09-05.* Two tasks from one routine share structure, identifiers and global
references, so task-level splitting leaks. But routine-level splitting also leaks: VistA
carries the same algorithm under multiple namespaces. `GMTSUMX3.m` and `SROGMTS2.m` are
byte-identical after comment stripping — same tags, same word lists, same typo
(`MOUNTIAN`) — because someone copied the Health Summary routine into the Surgery
namespace in 2001. There are **40 near-duplicate clusters** in the eligible pool. If
`GMTSUMX3` lands in train and `SROGMTS2` in eval you have leaked, and the lock file will
say you did not.

1. Enumerate candidate routines.
2. Cluster near-duplicates (`select.py` emits `duplicate_clusters` and per-candidate
   `duplicate_cluster` / `duplicate_siblings`).
3. Partition the **cluster list** 70/30, so siblings never straddle the split.
4. Write `data/tasks/split.lock.json` with both lists and a content hash.
5. Never write that file again. Every generator reads it.

If asked "did you train on your eval?", the answer must be a file, not a claim.

### Routine selection

Rank by tractability, not interest.

- **Prefer** computational routines — validation, formatting, date arithmetic, lookup logic
- **Avoid** RPC broker entry points, screen/UI handlers, terminal I/O, job spawning.
  Use VistA's own dictionaries rather than guessing from names: `^XWB(8994,` lists 1,552
  RPC entry points, `^DIC(19,` field 26 lists 2,594 menu options, `^ORD(101,` field 20
  lists 1,245 protocol entry actions, `^XPD(9.6,` lists 3,769 KIDS install routines.
- **Score** by call-graph fan-out (lower better) and distinct global references (lower
  better), plus **transitive reach at 3 hops** — a routine with fan-out 1 and no globals of
  its own can still pull in six globals through a callee that reads its arguments off the
  symbol table (`AJETIU4` does exactly this via `VADPT`).
- **Score by entry coverage** — the fraction of code lines reachable from a formal-argument
  entry. *Added 2026-09-05; it is the single most discriminating signal.* The mixed-case
  family (`LEXXM2/3/4/5/6`, `GMTSUMX2/3`) scores well on every obvious metric — small, no
  globals, no fan-out, string manipulation — and is uniformly bad, because the real logic
  sits in tags reading `X`, `LEXORG`, `LEXPRE` from the caller's frame and only trivial
  helpers take formal arguments. Entry coverage drops all of them (LEXXM2 0.10,
  GMTSUMX3 0.14).
- **Watch for local-variable leakage.** A routine with no `NEW` (e.g. `PSBVT1`) corrupts
  the caller's symbol table. Global-state diffing will pass while the damage is real.
- Target 40–60 routines yielding 200+ tasks. Supply is not the constraint: 1,942 routines
  survive hard exclusion and the top 60 alone offer ~380 clean formal-argument entry
  points. Killability and input-suite separation will bind first.

### Input suite generation

1. Static analysis of parameters and referenced globals.
2. Sampling real values from VEHU's populated globals — the advantage of using a container
   with synthetic patient data over bare YottaDB.
3. Boundary values: empty string, zero, negative, very long strings, undefined locals,
   missing subscripts.

Minimum 5 cases per task. Discard tasks where no case separates the versions.

### Metrics

| Metric | Definition |
|---|---|
| **pass@1** | Fraction where the first candidate verifies equivalent. Headline. |
| **pass@3** | Three independent attempts. |
| **repair iterations** | Mean verifier calls before success (tools-on). Shows the loop working. |
| **per-operator pass rate** | By mutation class. Makes the chart interesting. |
| **false-confidence rate** | Model asserted correctness but verification failed. **Most important number in the project.** |

That last metric *is* the pitch. It quantifies "fluent and confidently wrong."

### Conditions

1. **Baseline** — stock model, file access only, no tools
2. **Scaffolded** — same model, Rosetta tools including `verify_change`
3. **Tuned + scaffolded** — comprehension fine-tune plus tools *(cut candidate)*

Same tasks, seeds and attempt budget across conditions. Full traces to `results/*.jsonl`
so every published number is auditable. **No hand-typed numbers on the website, ever.**

---

## 10. Website specification

Single self-contained HTML file. No build step, no framework, inline CSS and JS.
Zero dependencies means zero chance it breaks at hour 35. Reads `results/summary.json`.

1. **Hero** — "Ground truth for code nobody can read." One-line thesis. Two buttons:
   *See the benchmark* / *Install*.
2. **The stakes** — F-35 / CMS-2 / JOVIAL framing, GAO figures, the ~$66B number. Lead
   with defense, not the VA.
3. **The side-by-side** — GIF or video of the money moment: agent without verifier looks
   correct, agent with verifier catches the regression.
4. **How it works** — four-step diagram: snapshot → change → execute both → diff outputs
   and global state.
5. **Benchmark results** — live table from `results/`. Three bars, per-operator breakdown.
   State the mutation-proxy limitation openly.
6. **Install** — `docker run` + `pip install` + OpenCode config snippet, with copy buttons.
7. **Deployment** — air-gapped by construction, MCP-portable, inherited ATO (Game Warden /
   Platform One), the ladder: public VistA → CUI COBOL → ITAR Ada/JOVIAL → classified CMS-2.
8. **Footer** — team, GitHub, DNHacks.

Build from hour 0 in parallel. It has no dependencies and it's the artifact that survives
the weekend.

> **Current state:** satisfied. The landing page is `index.html` plus `src/styles.css` and
> `src/main.js` — static files with original canvas artwork, no bundler, no package
> installation. A dependency-free Node script copies them to `dist/` for Vercel. The page
> states that published benchmark results are pending rather than showing sample figures;
> the contract those results must satisfy lives in `rosetta/bench/report.py`. An
> account/login portal was proposed and **removed**: Rosetta holds no customer credentials
> or code, and a sign-in surface contradicts the air-gapped pitch. `tests/web.test.mjs`
> guards the page's structure, its local assets, and that no percentage is hand-authored.

---

## 11. Instructions for coding agents

### Hard rules

1. **`rosetta/core/interface.py` is frozen.** Do not modify dataclasses or signatures. If a
   change seems necessary, stop and report rather than editing. Every stream builds on it.
2. **Stay in your directory.** You own exactly one stream. If you need something from
   another module, code against the contract and stub locally.
3. **Only `rosetta/core/` may touch YottaDB.** No `docker`/`ydb`/`mumps` subprocess calls
   elsewhere. Need execution? Call `rosetta.core.execute()`.
4. **Always snapshot before executing, always restore after.** Use `clean_state()`. An
   unrestored run corrupts state for every subsequent test, silently and confusingly.
5. **Never rewrite `data/tasks/split.lock.json`.** It is the proof we didn't train on eval.
6. **No new dependencies without asking.** The demo must run offline from a cold start.

### Style

- Python 3.11+, type hints on public functions, dataclasses over dicts.
- **Fail loudly.** A silent `except: pass` in the verifier makes every benchmark number
  meaningless. Raise, log the M error code, move on.
- No cleverness in the verifier. The whole project rests on its correctness — write it
  boringly and test it directly.
- Docstrings on anything another stream calls; skip them elsewhere.

### Testing

- `rosetta/core/` needs real tests; everything else needs a smoke test.
- **Canonical test:** take a known routine, apply a `CMP_FLIP`, assert
  `verify_equivalence` returns `equivalent=False` with a divergence naming the specific
  global or output that moved. If that passes, the project works.
- **Do not mock YottaDB in core tests.** Mocked verification proves nothing.

### Reporting back

Report what you built, what you stubbed, what you could not verify, and anything
contradicting this document. Do not report success if a test fails or a dependency is
missing — say so plainly. A known gap is manageable at hour 20; a hidden one is fatal at
hour 34.

---

## 12. The pitch

### Framing per audience

**Program offices** — we sell you the instrument that tells you whether the AI was wrong.
Verification, not promises. And we never need to see your code.

**Senior officials** — GAO-25-107795: 8 of 11 critical federal legacy systems on outdated
languages, 7 with known vulnerabilities. VA EHR replacement reached 5 of 150 medical
centers in five years.

**VCs** — the moat is the eval and the corpus, not inference plumbing. Benchmarks compound
and define procurement criteria. MCP-portable if the labs eat the agent layer. SBIR Phase
III converts a proven Phase II into indefinite sole-source revenue.

**OpenAI** — a measured capability gap on a domain nobody has instrumented, closed with
RFT and an executable grader.

### Demo script — three minutes

1. **The stakes.** F-35 runs on millions of lines. Navy tactical on CMS-2. Air Force
   avionics on JOVIAL. Roughly two-thirds of a $66B Pentagon IT budget goes to maintaining
   code whose last programmers are retiring.
2. **Lead with failure.** Frontier model, no tools, real VA code. Fluent, confident,
   wrong. Our harness catches the regression it introduced.
3. **Tools on.** Agent executes, verifies, catches its own break, repairs. Green.
4. **Scoreboard.** Three bars. False-confidence rate side by side.
5. **Why it generalizes.** The RFT grader insight below.

**The single moment to engineer:** side-by-side on one screen. Left, agent without the
verifier makes a change, explains itself fluently, looks completely correct. Right, same
task, verifier on — red: *"`^DPT` lookup now fails for patients with no middle name."*

That contrast is the entire company in one frame, and it's what a judge describes to
someone else later. Everything else is setup.

If you can make it interactive — a judge types a change request and watches it verify —
engagement changes completely. Gate it behind a known-good canned path so a crash can't
kill you.

### The line to land

> Everyone else is blocked on data. We replaced the dataset with an interpreter. Our
> verifier grades the benchmark, feeds the fine-tune, and *is* the reward function — so
> this works for JOVIAL or CMS-2, and **we never need to see your code.**

That last clause is what a program office reacts to. It's also the whole air-gap story in
seven words.

### What actually wins hackathons

Ranked honestly:

1. **A demo that doesn't crash.** Not close. Working beats better.
2. **One memorable moment.** Judges retain roughly one thing per team. Engineer it.
3. **Legible stakes in 15 seconds.** Fatigued judges. Concrete consequence, not jargon.
4. **Evidence you talked to a real stakeholder.** Most underrated winning move.
5. **A credible answer to "what happens next."** Most teams have none.
6. **Not overclaiming.** At a natsec event the audience is professionally skeptical.
   Naming a limitation buys more credibility than polish does.

**Traps:** technical sophistication for its own sake (a fine-tune nobody can evaluate is
worth less than a tool that visibly works); breadth (reads as shallow); slides; the word
"platform."

---

## 13. After the hackathon

### Sequencing

Use the eval and the MUMPS work as a beachhead into an agency relationship, then climb the
classification ladder. Public VistA → CUI Treasury/state COBOL → ITAR Ada and JOVIAL →
SECRET CMS-2. Do not start at the top.

### Go-to-market

- **Entry:** SBIR/STTR Phase I with AFWERX, DIU, or a civilian agency, targeting one
  language-and-domain pair rather than "legacy code" generally.
- **Deployment:** inherit an ATO via Second Front's Game Warden or Platform One. Never
  pursue your own — that's 6–18 months of RMF.
- **First contracts:** expect to subcontract under a prime holding the FCL and the vehicle.
- **The prize:** SBIR Phase III sole-source authority.

### What's defensible

The benchmark and corpus, not the inference. There is no JOVIAL benchmark, no CMS-2 test
suite, no accepted acceptance criterion. Building the first credible evaluation requires
compiler licenses and domain SMEs, not GPUs — which is exactly what a foundation-model lab
won't casually replicate.

The higher-value product for embedded and avionics work is likely **verified specification
recovery** rather than translation, because translated code needs requalification
(DO-178C and equivalents) regardless, and the certifiable spec is the artifact the program
office actually needs.

### Honest obstacles

Procurement runs 12–24 months. Incumbents own the vehicles. Agencies buy outcomes, not
tools. Per-language markets are a portfolio of $10–50M niches, not one large market. Code
inside enclaves is your moat once you're in and your bootstrapping problem before. And
Anthropic and OpenAI both already have government offerings, so the platform layer may get
commoditized — defensibility has to be the eval, the corpus, and the language-specific
tooling.

---

## 14. Appendix: MUMPS primer

What you need to know to write mutation operators and read routines.

- **Globals** are persistent sparse arrays prefixed `^` (e.g. `^DPT(3,0)`). They *are* the
  database — there is no separate storage layer. Locals have no sigil.
- **Naked references** (`^(3)`) reuse the last global reference's subscripts. A common
  source of real bugs and an excellent mutation target.
- **Postconditionals** attach to commands with `:` — `SET:X>3 Y=1`. Dropping one is subtle
  and high-quality as a mutation.
- **`$DATA`** tests existence, **`$GET`** retrieves with a default, **`$ORDER`** walks
  subscripts. Confusing them is realistic legacy breakage.
- **`$PIECE`** and **`$EXTRACT`** do delimited and positional substring extraction —
  off-by-one here is the classic legacy bug.
- **Routines** live in `.m` files, one routine per file, entry labels in column 1.
- **Errors** surface as codes: `M6` (undefined local), `M7` (undefined global). Capture
  them — an error *is* a valid divergence and is often the whole signal.
- **FileMan** is VistA's data dictionary layer, storing schema in globals like `^DD` and
  `^DIC`. `resolve_global` reads it to explain what a global means.
- **YottaDB** and **GT.M** are open-source M implementations. YottaDB is the current
  actively-developed fork.

### Container reference

```bash
docker pull --platform linux/amd64 worldvista/vehu
docker run -d --name vehu --platform linux/amd64 \
  -p 9430:9430 -p 8001:8001 -p 2222:22 worldvista/vehu
```

`worldvista/vehu` is FOIA VistA with synthetic patient data on YottaDB — real public
federal code, real populated globals, no PHI exposure. Alternatives:
`worldvista/osehravista`, `worldvista/worldvista-ehr`. Post-start setup takes several
minutes.

---

## 15. Sources

**Government reports and programs**

- [GAO-25-107795, Agencies Need to Plan for Modernizing Critical Decades-Old Legacy Systems](https://www.gao.gov/products/gao-25-107795)
- [FedScoop — GAO flagged 10 critical legacy IT systems; most haven't been modernized](https://fedscoop.com/the-gao-flagged-10-critical-legacy-it-systems-years-later-most-havent-been-modernized/)
- [DOL Unemployment Insurance Modernization](https://www.dol.gov/agencies/eta/ui-modernization)
- [DefenseScoop — Pentagon using AI to modernize legacy code](https://defensescoop.com/2024/09/12/pentagon-artificial-intelligence-modernize-legacy-code-john-hale/)
- [FedRAMP AI](https://www.fedramp.gov/ai/)

**Market and companies**

- [Code Metal — $125M Series B, $1.25B valuation](https://www.eweek.com/news/code-metal-valuation-ai-code-translation/)
- [Code Metal — $80M WarMatrix OTA](https://www.techtimes.com/articles/324602/20260815/pentagon-paid-code-metal-80m-prove-wargame-ai-wont-break-old-simulations.htm)
- [Mechanical Orchard Series A](https://www.mechanical-orchard.com/insights/mechanical-orchard-raises-24m-in-series-a-round-to-solve-the-legacy-it-modernization-challenge)
- [Hypercubic seed round](https://siliconangle.com/2026/08/18/hypercubic-raises-5-3m-map-rewrite-legacy-cobol-apps-ai-agents/)
- [Kodesage seed round](https://tech.eu/2026/06/04/kodesage-raises-66m-for-ai-powered-legacy-software-modernisation/)
- [GovTech — New AI tool aims to help agencies with COBOL problems](https://www.govtech.com/biz/new-ai-tools-aims-to-help-agencies-with-cobol-problems)
- [TSRI — MUMPS to Java, Veterans Health Administration](https://tsri.com/case-studies/mumps-to-java-veterans-health-administration)

**Research**

- [Knowledge Transfer from High-Resource to Low-Resource Programming Languages (MultiPL-T)](https://arxiv.org/abs/2308.09895)
- [Enhancing Code Generation for Low-Resource Languages: No Silver Bullet](https://arxiv.org/html/2501.19085)
- [Survey on LLM-based Code Generation for Low-Resource and Domain-Specific Languages](https://arxiv.org/html/2410.03981v3)

**Modernization practice**

- [mLogica — COBOL Is Not the Problem: The Hidden Estate Gap](https://www.mlogica.com/resources/blogs/cobol-is-not-the-problem)
- [A CTO's Guide to CICS Transaction Modernization](https://softwaremodernizationservices.com/insights/cics-transaction-modernization-approaches/)
- [AWS — Extracting business rules and program flows from mainframe COBOL](https://repost.aws/articles/ARiH1NPWyBQIqCR0j_T40K5g/extracting-business-rules-and-program-flows-of-mainframe-cobol-applications-using-aws-transform-for-mainframe)

**VistA / MUMPS**

- [WorldVistA docker-vista](https://github.com/WorldVistA/docker-vista)
- [worldvista/vehu on Docker Hub](https://hub.docker.com/r/worldvista/vehu)
- [worldvista/osehravista on Docker Hub](https://hub.docker.com/r/worldvista/osehravista)
- [Install VistA on GT.M or YottaDB (hardhats.org)](https://www.hardhats.org/projects/New/InstallVistAOnGTM.html)
- [VistA Docker Images — Sam Habiel, YottaDB](https://yottadb.com/wp-content/uploads/2022/11/221001-vista-docker-image.pdf)

---

*Figures cited from secondary sources (market sizing, migration failure rates, batch job
counts) should be treated as indicative rather than precise. Primary GAO and DOL figures
are as reported in the linked documents.*
