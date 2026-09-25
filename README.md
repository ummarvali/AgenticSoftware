# Agentic SDLC — Requirement → Reviewable Engineering Outcome

[![CI](https://github.com/ummarvali/AgenticSoftware/actions/workflows/ci.yml/badge.svg)](https://github.com/ummarvali/AgenticSoftware/actions/workflows/ci.yml)

An **agentic software-engineering system**, operated as a **GitHub Actions pipeline**: a
requirement arrives as an issue, a maintainer approves the spend, agents drive it across the
SDLC in Actions — understand → decompose → orchestrate → generate → **validate** — the result
(code, API contract, tests, docs and a structured engineering summary) is posted on the issue,
and an accepted result becomes a pull request. **Controlled autonomy**: agents act, humans
approve.

> Built for the mandatory use case: *"Build a scalable URL shortener service with APIs,
> persistence, and analytics."* — and it also handles greenfield work, brownfield changes
> to an existing repository (enhancements, bug fixes, refactors, test and documentation
> improvements — proposed as a validated change set, never applied), and ambiguous
> requirements. (Recorded brownfield evidence: rate-limiting enhancements, from the CLI and
> through the pipeline in PRs #3 and #5, plus the ambiguous performance change in PR #7; bug
> fixes, refactors and test/doc changes take the same change-mode path, not yet recorded.)

- **Model-driven by design** — a live LLM (Claude / OpenAI / Azure / any OpenAI-compatible
  endpoint) analyses the requirement, plans the task graph, designs the architecture and
  **authors the code and tests**. That is the primary mode, and the one the recorded
  evidence in [`examples/llm-run*/`](examples/) was produced with.
- **Never LLM-dependent** — every model stage is wrapped with timeouts, bounded retries and a
  per-stage fallback to a deterministic engine, with token/cost/latency **observability**.
  The same fallback lets a reviewer run and test everything without a key.
- **The system verifies its own output** — generated code is compiled and its tests are
  executed before a human is asked to accept the run.

---

## ⚡ TL;DR — for the reviewer

The agent runs as a **GitHub Actions pipeline**, the way a team would operate it: the model
key lives in GitHub, humans approve at two approval gates (spend, acceptance), and the output arrives as a pull request.
Three ways in:

| | How | What you need |
| --- | --- | --- |
| **A. Run it here, on GitHub** (recommended) | open an *Agent request* issue and pick a test case | a GitHub account — no install, no key |
| **B. Run it in your own fork** | fork, add your key as a secret, *Run workflow* | an Anthropic key, ~5 minutes of setup |
| **C. Run it on your machine** | the CLI, with or without a key | Python 3.10+ |

### A. Run it here, on GitHub — no install, no key

1. Open an issue from the **[Agent request form](https://github.com/ummarvali/AgenticSoftware/issues/new?template=agent-request.yml)**, pick a test case and submit:
   - *Greenfield: URL shortener* — the mandatory use case
   - *Greenfield: another domain* — inventory with low-stock alerts
   - *Brownfield enhancement* — add rate limiting to the existing service in `demo/`
   - *Vague requirement* — "Make the app faster." against `demo/`
   - *Custom* — your own new project, or your own bug fix / refactor / tests / docs for `demo/`

   These cover the brief's scope: greenfield, brownfield (enhancement, and bug fix or refactor
   as a custom change), and test and documentation improvements (custom change). Well-defined
   vs ambiguous is **not something you select**: the RequirementAnalyst detects ambiguity in
   whatever text arrives, lists the open questions and records a default assumption for each
   (shown in the result). The vague preset simply supplies a requirement that needs this.
2. Within seconds the issue gets a link to its **pipeline run**. The run waits until the
   maintainer approves the spend (the `agent-run` gate: the model key is the maintainer's and
   is never visible to anyone, including in logs).
3. Watch it work: **agent run → Run the agent** streams every step live — the task graph, each
   agent's decision, one `[LLM]` line per model call (stage, tokens, latency, retries), the
   sandbox validation and the in-run gates. A run takes ~6–12 minutes after approval.
4. The result is **posted on your issue**: each validation check, tokens and cost, and the full
   engineering summary. The run page shows the same summary, and its run-record artifact (named **`agent-run`**)
   holds the generated code, tests, `openapi.yaml`, `README.md` and `result.json`.
5. The maintainer reviews it and accepts or rejects (the `agent-acceptance` gate). Accepted →
   a **pull request** is opened and linked on your issue: a new project under
   `generated/<run-id>/`, or a brownfield change as a diff of the real `demo/` files.

One issue runs one test case. To try several, open one issue per test case; they run in
parallel, each with its own result comment and pull request.

```
issue (test case) ─▶ ⏸ approve spend ─▶ agent run in Actions ─▶ result on the issue ─▶ ⏸ accept ─▶ pull request
```

Failures are shown, not hidden: a failed check, a halted run, or a model stage that fell back
to the offline engine stops the pipeline before acceptance, and the issue says so. How the
pipeline is built and secured: [§12](#12-operating-this-in-production--the-sre-view).

### B. Run it in your own fork — your key, your approvals

1. **Fork** this repository; in the fork's **Actions** tab, enable workflows.
2. Fork **Settings → Environments**:
   - `agent-run` — *Deployment branches and tags*: `main` only; *Environment secrets*:
     `ANTHROPIC_API_KEY` (from console.anthropic.com). Add yourself under *Required reviewers*
     to approve each spend.
   - `agent-acceptance` — *Required reviewers*: yourself.
3. Fork **Settings → Actions → General → Workflow permissions**: tick *Allow GitHub Actions to
   create and approve pull requests*, then Save.
4. **Actions → Agent pipeline → Run workflow**: a requirement (the mandatory one is
   pre-filled), `repo_path` = `demo` for a brownfield change, provider `claude`. To use the
   issue form in the fork, enable *Issues* under Settings → General → Features.

### Already recorded — inspect without running anything

Pipeline runs on GitHub, plus four earlier live runs from the CLI, checked in exactly as they came out of the agent:

| What you want to see | Where |
| --- | --- |
| **The pipeline, end to end on GitHub**: the mandatory requirement run in Actions — spend approved, live console, 5/5 checks with 28 model-written tests, 88.5k tokens (~$0.67), accepted, pull request opened | [Actions run](https://github.com/ummarvali/AgenticSoftware/actions/runs/36131504163) → [pull request #1](https://github.com/ummarvali/AgenticSoftware/pull/1) (code under `generated/20260925-115009-176/`) |
| **A brownfield change requested through the issue form** — "add rate limiting" to `demo/`: 6/6 checks, the change validated with `demo/`'s 20 existing tests plus 21 new ones, 49k tokens (~$0.37). Code review of the PR then found two security gaps the automated gates cannot see (an unauthenticated admin endpoint; a client-chosen `Authorization` value used as the quota key) — which is what the acceptance gate and PR review are for, and it led to a SECURITY rule in the code-generation prompts | [issue #2](https://github.com/ummarvali/AgenticSoftware/issues/2) → [pull request #3](https://github.com/ummarvali/AgenticSoftware/pull/3) |
| **The same request after that finding** — re-run once the SECURITY rule was in the prompts: 6/6 checks, 36 tests (20 existing + 16 new); limits are configuration only (no admin endpoint), and a client key counts only if it matches a server-side list, else the peer address is used. Verified by running it: the 4th request over a limit of 3 gets 429 with `Retry-After`, rotating fake keys stays at 429, `/admin/*` is 404. ~54k tokens (~$0.41) | [issue #4](https://github.com/ummarvali/AgenticSoftware/issues/4) → [pull request #5](https://github.com/ummarvali/AgenticSoftware/pull/5) |
| **An ambiguous requirement through the issue form** — "Make the app faster." against `demo/`: the analysis records its interpretation as explicit assumptions (backend/API and database are the targets; no profiling data exists yet; code-level changes only, no new infrastructure), and the change set is validated 6/6 with 35 tests. The model chose a broad reading — a profiling toolkit plus an opt-in batched-commit mode in the store, not a default speed-up — which is exactly what the acceptance review is there to judge (see §9) | [issue #6](https://github.com/ummarvali/AgenticSoftware/issues/6) → [pull request #7](https://github.com/ummarvali/AgenticSoftware/pull/7) |
| The mandatory URL shortener: code and tests **authored by the model**, sandbox-validated | [`examples/llm-run/`](examples/llm-run/) — `artifacts/` (SQLite-backed service + its own tests) and `result.json` (per-stage tokens, latency, cost, retries, fallbacks) |
| A different domain through the same agents (inventory + low-stock alerts) | [`examples/llm-run-inventory/`](examples/llm-run-inventory/) |
| **Brownfield**: a change to an existing repository (`--repo demo`, "add rate limiting") — the model returns only the changed files, validated with demo's own tests re-run on a copy with the change applied | [`examples/llm-run-brownfield/`](examples/llm-run-brownfield/) — `CHANGES.diff`, the changed files, and the *Proposed change set* table in `ENGINEERING_SUMMARY.md` |
| A limit made explicit: a **Go** requirement — the design records Go, the validated slice is Python (stated as a limitation in the summary), and its model-written tests pass | [`examples/llm-run-go-card-validator/`](examples/llm-run-go-card-validator/) |
| The report every run ends with: plan, rationale, design↔implementation coverage, validation, risks, trade-offs, assumptions, limitations | `artifacts/ENGINEERING_SUMMARY.md` in each folder above |
| How the agent is built and why | [§4](#4-how-it-works--architecture--control-flow), [§8](#8-risks-trade-offs--validation), [§12](#12-operating-this-in-production--the-sre-view) |

Each generated service runs on its own (`examples/llm-run*/artifacts/README.md` says how),
and its tests pass from the checkout: `cd examples/llm-run/artifacts && python3 -m unittest discover -s tests`.
The brownfield change set is re-verified against `demo/` with
`python3 scripts/verify_change.py examples/llm-run-brownfield --repo demo` (copies `demo/`,
applies the change, runs the existing tests plus the new ones). `scripts/evaluate.py` does
both for **every** recorded run each time it runs — the recorded code is re-tested, not just
its logs.

### C. Run it on your machine

**With a key** (the live model):

```bash
git clone https://github.com/ummarvali/AgenticSoftware
cd AgenticSoftware
python3 -m venv .venv                              # PowerShell: python -m venv .venv
source .venv/bin/activate                          # PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[anthropic]"                      # or ".[llm]" for OpenAI / Azure / Gemini
export ANTHROPIC_API_KEY="<your key>"              # PowerShell: $env:ANTHROPIC_API_KEY = "<your key>"
python -m agentic_sdlc --provider claude --file examples/greenfield.txt
python -m agentic_sdlc --provider claude --interactive --file examples/greenfield.txt   # approve the 3 in-run gates yourself
```

The model analyses the requirement, plans a 15–30-task graph, designs the service, writes the
code, its `openapi.yaml`, its README **and** its tests (unit tests plus integration tests that
drive every endpoint over HTTP, including error cases); the code is accepted only after it
passes a static safety scan, compiles, and its own tests pass in a sandbox (one repair pass
with the real error output if they don't). The result lands in `runs/<run-id>/`, ending with
`artifacts/ENGINEERING_SUMMARY.md`. Live runs used `claude-sonnet-5` and cost $0.37–0.67 each
at its published $2 / $10 per-million-token price; token counts are always
reported, and a dollar figure only when the model's price is known (otherwise `cost n/a`).
**No key is committed anywhere in this repository.**

**Without a key — no third-party packages.** The same agents, gates and validator,
with the deterministic engine in place of the model (this is also what CI runs):

```bash
export PYTHONPATH=src                                   # PowerShell: $env:PYTHONPATH = "src"
python3 -m unittest discover -s tests                   # 75 tests, OK
python3 -m agentic_sdlc --file examples/greenfield.txt  # plan, build, validate, report — offline
python3 scripts/evaluate.py                             # scorecard: 6 offline scenarios + the 4 recorded live runs, re-tested now
```

The fallback's output for the mandatory requirement is committed as [`demo/`](demo/) — a
service you can start in ten seconds:

```bash
cd demo && python -m url_shortener.server          # http://127.0.0.1:8000 — run the curls below in a second terminal
curl -X POST http://127.0.0.1:8000/api/shorten -H "Content-Type: application/json" -d "{\"url\": \"https://example.com/a/very/long/path\"}"
curl -i http://127.0.0.1:8000/<code>               # 302 redirect
curl http://127.0.0.1:8000/api/stats/<code>        # click analytics
```

Containers: `docker build -t agentic-sdlc .` (the agent; pass `-e ANTHROPIC_API_KEY` for the
live mode) and `docker build -t url-shortener demo` (the demo service).

---

## How I approached this — candidate notes

1. **I read the brief as an SRE problem, not a chatbot problem.** "Controlled autonomy" and
   "validation and risk control" are reliability requirements. So the first design decision
   was: the system must *verify its own output* (compile + run the tests it wrote) before a
   human is ever asked to approve, and it must *never half-complete* — every failure path
   retries, degrades, or halts cleanly with the partial record saved.
2. **I separated the brain from the workflow.** Agents contain no domain knowledge; they
   call a swappable `ReasoningProvider`. That let me build and test the orchestration
   (DAG, gates, retry, repair loop) fully offline and deterministically, then plug in a
   real model (Claude / OpenAI / Azure / any OpenAI-compatible endpoint) without touching
   an agent. The offline engine is not a stand-in for the model — it is the model's
   **fallback**, per stage, which is how I would want a production agent to behave.
3. **I chose a blackboard over agent-to-agent calls.** With one shared state and one event
   log, a run is auditable end to end (every agent decision is logged with its rationale),
   each agent is unit-testable alone, and adding a concurrent executor for independent
   tasks needed only a lock on the log.
4. **The model builds the service; my job was the gate that proves it runs.** Model-authored
   code is accepted only after a static safety scan, compilation and its own tests pass in
   a sandbox, with one repair pass fed by the real error. The deterministic template
   behind it is deliberately boring — standard library, base62 ids, in-memory or SQLite,
   WSGI, OpenAPI, unit + integration tests, a container image — so the fallback is always
   runnable in ten seconds.
5. **I wrote down what I did not do.** Predictable sequential codes, synchronous click
   recording, a heuristic brownfield scan, a sandbox that is not network-isolated — see
   §8–§9. In a prototype the trade-offs matter more than the feature count.
6. **I ran it the way a team would operate it.** A requirement arrives as an issue, the agent
   runs in GitHub Actions with the key in the secret store, a named person approves the spend
   and then the result, and the output enters normal code review as a pull request (§12).

I used AI coding assistants as pair-programmers during implementation; the architecture,
the reliability stance above, and the review of every module are mine.

---

## Design philosophy — LLM-first, with a reliability fallback

The reasoning layer is a swappable seam (`ReasoningProvider`), so the *same* agents and
orchestration run on any brain:

- **LLM backend (Claude / OpenAI / Azure OpenAI / any OpenAI-compatible endpoint)** — the
  primary mode: the model drives requirement **analysis, task decomposition, architecture
  design, and code + test generation**. It is used automatically when a key is present
  (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, or `OPENAI_BASE_URL`) — no
  code change. `ANTHROPIC_MODEL` / `OPENAI_MODEL` pin the model; otherwise the first Sonnet
  model the Anthropic account can list is used (the recorded runs used `claude-sonnet-5`).
- **Deterministic engine (default when no key, and the fallback for every LLM stage)** — a
  reproducible rule engine that **backs each model call**: on a timeout, error, or malformed
  response the pipeline degrades to it per-stage, so a run never half-completes.

Every model call is wrapped with a **timeout, bounded retries, schema-checked JSON** (parsed
leniently — code fences, surrounding prose and raw tabs/newlines inside source strings are
tolerated; the *shape* is what is validated), and **per-stage fallback**. **Every run** (LLM or offline) emits **monitoring** — wall-clock
duration, task count, retries, repairs, degradations, and gate decisions — plus LLM tokens,
latency, cost, and fallback count when a model is used (printed and saved in `result.json`).
Failures can be **injected on demand** (`--inject-fault code:1`) to demonstrate recovery.

This is the SRE stance made concrete: generation is genuinely requirement→code, and the
sandbox gate (§8) decides whether the model's code is accepted.

---

## Table of contents

1. [Why this design](#1-why-this-design)
2. [Quick start (setup instructions)](#2-quick-start-setup-instructions)
3. [The mandatory use case, end to end](#3-the-mandatory-use-case-end-to-end)
4. [How it works — architecture & control flow](#4-how-it-works--architecture--control-flow)
5. [Example scenarios](#5-example-scenarios)
6. [Project layout — what every file does](#6-project-layout--what-every-file-does)
7. [Testing approach](#7-testing-approach)
8. [Risks, trade-offs & validation](#8-risks-trade-offs--validation)
9. [Assumptions & limitations](#9-assumptions--limitations)
10. [Requirement-coverage matrix](#10-requirement-coverage-matrix)
11. [Extending the system](#11-extending-the-system)
12. [Operating this in production — the SRE view](#12-operating-this-in-production--the-sre-view) — the team pipeline (`agent.yml`)

---

## 1. Why this design

The brief asks for *end-to-end workflow automation across the SDLC, not a generic chatbot*.
Three principles shape the implementation:

1. **Separate the brain from the workflow.** Agents contain no domain knowledge; they call a
   swappable `ReasoningProvider`. This makes the orchestration testable and lets the *same*
   pipeline run offline (deterministic) or on a live model.
2. **Coordinate through shared state, never agent-to-agent calls.** Every step reads and
   writes one `Blackboard`, so a run is fully auditable (an event log explains what happened
   and why) and each agent is independently unit-testable.
3. **Autonomy must be controlled and verifiable.** Work runs as a dependency **DAG** with
   retry/degrade/halt recovery, human **approval gates** at defined checkpoints, and a
   **validator that actually compiles and runs** the generated code.

---

## 2. Quick start (setup instructions)

**No setup at all:** run it on GitHub from an issue — [TL;DR A](#a-run-it-here-on-github--no-install-no-key).
The rest of this section is for running it on your own machine.

**Requirement:** Python 3.10+ (developed on 3.12; CI runs 3.10 and 3.12 on Linux and
Windows). No third-party packages are needed for the core system — the model SDKs are
optional extras.

**Primary mode — live model.** Set a key and pass `--provider claude` (or `openai`);
the commands are under **Run on a real model** further down this section. Without a
key, every command in this section runs on the deterministic fallback.

**One-command narrated demo** (all scenarios + fault injection + monitoring):

```powershell
python scripts/demo.py            # add --provider claude to run on a live model
```

Or drive it yourself — **Linux / macOS / WSL**:

```bash
cd AgenticSoftware
export PYTHONPATH=src
python3 -m agentic_sdlc "Build a scalable URL shortener service with APIs, persistence, and analytics."
python3 -m unittest discover -s tests -v          # 75 framework tests
python3 -m agentic_sdlc --interactive --file examples/greenfield.txt   # human approves each gate
```

**Windows PowerShell**:

```powershell
# From the folder you cloned into
cd AgenticSoftware

# Option A — run directly (no install), just put src on the path:
$env:PYTHONPATH = "src"
python -m agentic_sdlc "Build a scalable URL shortener service with APIs, persistence, and analytics."

# Option B — install as a package (adds the `agentic-sdlc` command):
python -m pip install -e .
agentic-sdlc "Build a scalable URL shortener service with APIs, persistence, and analytics."
```

> On Windows the interpreter may be `py`, `python`, or a full path such as
> `C:\Users\<you>\.local\bin\python3.12.exe`. Substitute accordingly.

**Interactive mode (human approves each checkpoint):**

```powershell
python -m agentic_sdlc --interactive --file examples/greenfield.txt
```

**Run the tests:**

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v          # framework tests (no pytest needed)
python -m pip install -e ".[dev]"; pytest        # or with pytest
```

**Run on a real model (Claude):**

The system was developed and exercised end to end against a real Anthropic key; the pipeline
runs ([PR #1](https://github.com/ummarvali/AgenticSoftware/pull/1), [PR #3](https://github.com/ummarvali/AgenticSoftware/pull/3), [PR #5](https://github.com/ummarvali/AgenticSoftware/pull/5), [PR #7](https://github.com/ummarvali/AgenticSoftware/pull/7)) and the four CLI runs under `examples/llm-run*/` are the evidence. **No key is committed anywhere in
this repository and none is needed to run, test or evaluate it** — without a key the same
pipeline runs on the deterministic engine. To reproduce a live run yourself:

```powershell
pip install -e ".[anthropic]"
$env:ANTHROPIC_API_KEY = "<your key>"   # from console.anthropic.com (NOT a claude.ai login); never commit it
python -m agentic_sdlc --provider claude "Build a scalable URL shortener service with APIs, persistence, and analytics."
# OpenAI/Azure instead: pip install -e ".[llm]"; set OPENAI_API_KEY (or AZURE_OPENAI_ENDPOINT); --provider openai
```

**Run on a free model (Gemini, via its OpenAI-compatible endpoint):**

```bash
pip install -e ".[llm]"
export OPENAI_API_KEY="<your Google AI Studio key>"        # free tier: aistudio.google.com
export OPENAI_BASE_URL="https://generativelanguage.googleapis.com/v1beta/openai/"
export OPENAI_MODEL="gemini-2.0-flash"
python3 -m agentic_sdlc --provider openai --file examples/greenfield.txt
```

A circuit breaker guards against runaway spend: `AGENTIC_LLM_MAX_CALLS` (default 200) and
`AGENTIC_LLM_MAX_COST_USD` (default 10.00) — far above a legitimate run (4–8 calls, well under a dollar;
for a model with no known price the breaker assumes the most expensive rate in the table, so it trips early);
if tripped, remaining stages degrade to the deterministic engine and the record says so.

Keys are read **only** from the environment and are never written to disk or to
`result.json`. To refresh a recorded run under `examples/` after your own run: `python scripts/snapshot_run.py --name llm-run` (copies the latest run and scrubs
anything key-shaped).

**CLI flags:** `--file <path>`, `--repo <path>` (brownfield: propose a change set against this repository), `--interactive`,
`--provider auto|deterministic|claude|anthropic|openai|llm`, `--inject-fault <category[:N]>`,
`--sequential` (disable in-level concurrency), `--output-root <dir>`, `--quiet`, `--json`
(machine-readable result on stdout, logs suppressed). Exit status is 0 only for a validated,
accepted run — usable as a CI step.

---

### Where the output goes — and why it never touches your code

Every run writes to an **isolated, timestamped workspace**: `runs/<run-id>/artifacts/`
(the generated project) plus `runs/<run-id>/result.json` (the full machine-readable record).
Override the root with `--output-root`. Nothing is written anywhere else.

- **Greenfield** — the whole new project lands in the workspace, runnable as-is.
- **Brownfield (change mode)** — `--repo <path>` is **read-only**. The CodebaseAnalyst
  snapshots it and ranks its files by relevance to the requirement; the model receives the
  relevant files and returns **only the files to add or change** — code, tests or docs
  (in a plain `<<<FILE path>>> … <<<END FILE>>>` format, so source travels verbatim with
  nothing to escape),
  whatever the requirement asks for (enhancement, bug fix, refactor, test or doc
  improvement). The change is validated on a **throwaway copy of the repository with the
  change applied**: static scan of the changed files, compile, then the repository's own
  test suite plus the new tests — a regression fails the gate (one repair pass, then the
  deterministic engine). The workspace receives the changed files and `CHANGES.diff`; the
  repository is never written. In the GitHub pipeline this is implemented: after the
  `agent-acceptance` approval the change is written to a branch and a pull request is opened
  (§12).

  ```bash
  python3 -m agentic_sdlc --provider claude --repo demo --file examples/brownfield.txt
  python3 -m agentic_sdlc --provider claude --repo demo "Fix the bug where …"      # any change
  ```

This is the same model as a CI job workspace or an artifact store: the outcome is a
*reviewable proposal*, not a change already applied. `runs/` is git-ignored — the
**recorded evidence** is the pipeline pull requests ([#1](https://github.com/ummarvali/AgenticSoftware/pull/1), [#3](https://github.com/ummarvali/AgenticSoftware/pull/3), [#5](https://github.com/ummarvali/AgenticSoftware/pull/5), [#7](https://github.com/ummarvali/AgenticSoftware/pull/7) — left unmerged on
purpose), `demo/` (the deterministic output for the mandatory requirement)
and `examples/llm-run*/` (four recorded live-model runs, copied from `runs/` by
`scripts/snapshot_run.py`, which scrubs anything key-shaped).

---

## 3. The mandatory use case, end to end

**Primary evidence:** the pipeline [run](https://github.com/ummarvali/AgenticSoftware/actions/runs/36131504163) → [pull request #1](https://github.com/ummarvali/AgenticSoftware/pull/1)
— the mandatory requirement through GitHub Actions, on `claude-sonnet-5`.

**What the agents do, step by step.** Condensed from the recorded event log and metrics of a
live run of this requirement ([`examples/llm-run/result.json`](examples/llm-run/)); in the
pipeline the same lines stream in the *agent run → Run the agent* console:

```
[orchestrator] provider: llm                          <- Claude; per-stage deterministic fallback
[LLM] analyze: ok in 17s (207+1516 tokens)
[RequirementAnalyst] kind=greenfield domain=url_shortener (7 ambiguities, 12 FRs)
[gate 1/3] requirement clarification: auto-approved   <- human with --interactive; in the pipeline
                                                         the humans approve spend and acceptance
[LLM] decompose: ok in 35s (256+4156 tokens)
[TaskDecomposer] 29 tasks, 13 levels                   <- the model's task graph, checked and
[gate 2/3] execution plan: auto-approved                  normalised before it is used
[Architect] decided: design(durable) - NFRs imply durability/scale -> recommend the SQLite backend as default
[LLM] design: ok in 33s (1612+3399 tokens)
[Architect] designed 12 components, 7 API endpoints
[orchestrator] level 3: running 5 tasks concurrently  <- independent tasks in parallel
[CodeGenerator] decided: generate - no code yet -> generate from the design
[LLM] codegen: ok in 283s (3242+38549 tokens)         <- code, openapi.yaml, README and tests in one
[CodeGenerator] generated 9 files                         bundle, accepted only after the sandbox gate:
                                                          safety scan, compile, its own tests
[Architect] decided: reuse - architecture already committed; 'design_security' is covered by it
  ... 23 of the 29 planned tasks are logged as reuse of the stage that already produced them
[TestGenerator] decided: generate-tests - generate unit + integration tests for the code
[DocGenerator] decided: generate-docs - generate README and architecture docs
[Validator] 5/5 checks passed                         <- compiled, scanned, Ran 24 tests - OK
[gate 3/3] final acceptance: auto-approved            <- never auto-accepts a failing report
[SummaryWriter] engineering summary written
[observability] 371s | 29 tasks, 8 parallel levels, 23 reused | 4 model calls, 52.9k tokens (~$0.49)
```

**What the model generated in the pipeline run** ([PR #1](https://github.com/ummarvali/AgenticSoftware/pull/1), code under
`generated/20260925-115009-176/`; 28 tests pass, and the service was exercised over HTTP:
create 201, redirect 302, analytics, 400/404/409 errors, SQLite tables):

| Artifact | Purpose |
| --- | --- |
| `urlshortener/storage.py` | SQLite persistence: urls, users, click events, blacklist |
| `urlshortener/shortcode.py`, `validation.py` | base62 short codes with collision checks; URL and alias validation |
| `urlshortener/cache.py` | in-process cache for the redirect path |
| `urlshortener/service.py` | create / resolve / delete / analytics logic |
| `urlshortener/api.py`, `server.py` | HTTP API (standard library), configurable host, port and database path |
| `openapi.yaml` | the API contract (OpenAPI 3.0.3) |
| `tests/test_service.py`, `tests/test_api.py` | 16 unit tests + 12 API tests over real HTTP |
| `README.md`, `ENGINEERING_SUMMARY.md` | how to run it (curl per endpoint); the engineering summary (also the PR description) |

### Offline fallback (no key)

The same requirement without a key runs the same agents on the deterministic engine — short,
reproducible, and what CI runs:

```
$ python -m agentic_sdlc "Build a scalable URL shortener service with APIs, persistence, and analytics."

[RequirementAnalyst] kind=greenfield domain=url_shortener (3 ambiguities, 5 FRs)
[TaskDecomposer] 6 tasks, 5 levels (design, code|docs, tests, validate, summary)
[Architect] designed 5 components, 4 API endpoints
[orchestrator] --- level 1: code, docs (parallel) ---
[CodeGenerator] generated 9 files
[DocGenerator] generated 2 documentation files
[TestGenerator] generated 3 test files
[Validator] 5/5 checks passed
Validation     : 5/5 checks passed (PASS)
```

Its output for this requirement is committed as [`demo/`](demo/) (base62 codes, in-memory or
SQLite store, WSGI API, `openapi.yaml`, unit and integration tests, a container image).
You can run the generated service directly:

```powershell
cd runs\<run-id>\artifacts
python -m url_shortener.server                       # http://127.0.0.1:8000
python -m unittest discover -s tests -v              # its own tests pass
```

---

## 4. How it works — architecture & control flow

```
 Issue (Agent request) ──► ⏸ agent-run: maintainer approves spend ──► GitHub Actions job
                                                                            │
 ┌────────────────────────────── Orchestrator (one run) ────────────────────▼──────────────────┐
 │                                                                                             │
 │  Analyst ──► Decomposer ──► DAG by dependency level (independent tasks run concurrently)    │
 │  (normalize) (task graph)   Architect ─► [CodebaseAnalyst: brownfield] ─► CodeGen           │
 │                             ─► TestGen ─► DocGen                                            │
 │      │           │              │                   │                                       │
 │      └───────────┴──────────────┴─── reasoning ─────┘                                       │
 │                  Claude (claude-sonnet-5): analyze · decompose · design · code + tests      │
 │                  └ per stage, on timeout / error / bad reply: deterministic fallback        │
 │                  model code ─► sandbox gate: safety scan → compile → its own tests          │
 │                               (one repair pass with the real failure)                       │
 │                                                                                             │
 │  Validator (5 checks; 6 for a change set) ── fail & repairable ──► Repair ─► re-validate    │
 │  every agent reads/writes the shared BLACKBOARD (state + decision log)                      │
 │  in-run gates: clarify · plan · accept (automatic in the pipeline; human with --interactive)│
 │                                                                                             │
 └───────────────────────────────────────────┬─────────────────────────────────────────────────┘
                                             ▼
      engineering summary + result.json ─► comment on the issue ─► ⏸ agent-acceptance ─► pull request
```

Full diagrams and rationale live in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). In short:

**Why these are agents, not functions.** Every agent runs an explicit
**perceive → decide → act** loop (`agents/base.py`): it observes the blackboard,
**chooses** an action and **records its rationale**, then acts. The choice is real and
adaptive — the `Architect` picks a persistence default from the NFRs, the
`CodebaseAnalyst` chooses *scan-repo* vs *design-only impact*, and the `Repair` agent
decides which findings it can fix. Every decision is emitted as a `decision` event, so a
run's reasoning is auditable end to end.

- **Bootstrap** — `RequirementAnalyst` classifies the requirement
  (greenfield / brownfield / ambiguous), normalizes it, and turns each ambiguity into an
  explicit default assumption. → **human clarification gate**.
- **Plan** — `TaskDecomposer` builds a **task DAG**; a topological sort groups it into
  dependency *levels*; tasks in one level have no mutual dependency, so the executor
  runs them **concurrently** (a thread per task, `--sequential` to disable). Level N+1
  never starts before level N completes — the ordering the graph guarantees. → **human
  plan gate**.
- **Idempotent agents** — a live model may plan many tasks of one category
  (`code-storage-layer`, `code-cache-layer`, …). Each agent *perceives* whether the work
  already exists and *decides* to **reuse** it (logged as a decision, counted as
  `reused=N`), so a rich plan never causes duplicate designs or duplicate artifacts.
- **Execute** — the orchestrator walks the levels and dispatches each task to the agent
  registered for its `category`, all coordinating through the `Blackboard`. Each task is
  wrapped in **retry-with-backoff**; persistent failures **degrade** optional tasks or
  **halt** required ones.
- **Validate → Repair (feedback loop)** — `Validator` compiles every `.py` file and runs the
  generated test suite in a sandboxed subprocess. If it reports a *repairable* finding
  (e.g. a missing API contract), the orchestrator routes control **back** to the `Repair`
  agent, which fixes it, and then **re-validates** — a bounded, agent-driven self-correction,
  not a blind retry. (Watch the ambiguous example heal `4/5 → 5/5`.)
- **Accept** — a final human gate reviews the validation report before `SummaryWriter`
  emits the summary and `result.json`.

**Controlled autonomy** = agents run independently across many steps, but the run pauses at
three gates (`ConsoleApproval` interactively, or `AutoApprove` for scripted/CI use), and any
gate rejection halts cleanly while still saving the partial result. **Every run prints which
gate mode is active and the outcome of each gate** (`[gate 1/3] … auto-approved` / `approved by
human`) and records it as a `gate` event, so a reviewer of a non-interactive run can see the
checkpoints were exercised. In the GitHub pipeline the in-run gates are automatic (`AutoApprove`,
which never accepts a failing report) and the human gates are GitHub Environment approvals —
before the run (spend) and after it (acceptance); see §12. `ApprovalGate` is the seam for any
other approval system (Slack, Jira, a web UI).

---

## 5. Example scenarios

**Live, through the pipeline** (requirement in the issue, output in the pull request — each PR
description is the full engineering summary: task plan, decisions, validation, risks):

| Scenario | Issue → pull request | Outcome |
| --- | --- | --- |
| Greenfield — the mandatory URL shortener | [run](https://github.com/ummarvali/AgenticSoftware/actions/runs/36131504163) → [#1](https://github.com/ummarvali/AgenticSoftware/pull/1) | 5/5 checks, 28 tests, new project under `generated/` |
| Brownfield — rate limiting on `demo/` | [#2](https://github.com/ummarvali/AgenticSoftware/issues/2) → [#3](https://github.com/ummarvali/AgenticSoftware/pull/3), then [#4](https://github.com/ummarvali/AgenticSoftware/issues/4) → [#5](https://github.com/ummarvali/AgenticSoftware/pull/5) | 6/6 checks; code review of #3 found two security gaps; #5, after the prompt fix, closes both |
| Ambiguous — "Make the app faster." on `demo/` (ambiguity detected by the agent, not declared) | [#6](https://github.com/ummarvali/AgenticSoftware/issues/6) → [#7](https://github.com/ummarvali/AgenticSoftware/pull/7) | 6/6 checks, 35 tests; interpretation recorded as assumptions; broader than a reviewer would want (§9) |

**Offline, reproducible without a key:** see [`examples/README.md`](examples/README.md) for
inputs, commands, and expected outputs for the **greenfield**, **brownfield**, and
**ambiguous** cases, plus the error-recovery demos. Highlights:

- **Greenfield** → full URL-shortener package, `5/5` checks pass.
- **Brownfield** ("Add rate limiting to the existing URL shortener…", `--repo demo`) → an
  extra `impact` task runs *before* `code`; the run proposes a **change set**, not a new
  project: `url_shortener/ratelimit.py` (new), `url_shortener/server.py` and `openapi.yaml`
  (modified), `tests/test_ratelimit.py` (new), plus `CHANGES.diff`. Validation runs demo's
  20 existing tests plus the 2 new ones on a copy of `demo/` with the change applied
  (6/6 checks); `demo/` itself is untouched. The evaluator fails the scenario if the
  limiter is missing or if the output regenerates the whole project. Offline, only this
  rate-limit change is authored; any other change against a repository needs the live
  model, and the offline run says so (`change set present: FAIL`) instead of faking it.
- **Ambiguous** ("Make the app faster.") → classified `ambiguous` (low confidence); every open
  question gets a recorded default assumption shown at the clarification gate; validation
  first fails 4/5 (no contract), the Repair agent adds it, re-validation passes 5/5. The
  control against a false "done" is the clarification gate — in `--interactive` mode a human
  answers or rejects there — not the validator. In the GitHub pipeline no human sees these
  assumptions before the run: they are listed in the summary posted on the issue and judged at
  the acceptance approval.

---

## 6. Project layout — what every file does

```
AgenticSoftware/
├─ Dockerfile / .dockerignore         Container image for the agent itself (`docker run agentic-sdlc …`)
├─ pyproject.toml                     Package metadata, entry point, pytest config
├─ requirements.txt                   Optional extras only (dev=pytest, llm=openai, anthropic=anthropic)
├─ .gitignore                         Excludes caches, venvs, and generated runs/
├─ README.md                          This document
├─ LICENSE                            MIT
├─ demo/                              ★ The generated URL shortener — runnable as-is
│  ├─ url_shortener/                  service package (base62, store, service, api, server)
│  ├─ tests/                          unit + integration tests (20, all pass)
│  ├─ openapi.yaml                    API contract
│  ├─ Dockerfile / .dockerignore      container image (non-root, healthcheck)
│  └─ README.md / docs/ / ENGINEERING_SUMMARY.md
├─ docs/ARCHITECTURE.md               Diagrams, control flow, design decisions
├─ examples/                          Three requirement inputs + expected outputs
│  ├─ greenfield.txt / brownfield.txt / ambiguous.txt
│  ├─ llm-run/                        ★ Recorded live-model run: URL shortener (result.json + artifacts)
│  ├─ llm-run-inventory/              ★ Recorded live-model run: inventory service
│  ├─ llm-run-brownfield/             ★ Recorded live-model run: change set against demo/ (CHANGES.diff)
│  ├─ llm-run-go-card-validator/      ★ Recorded live-model run: non-Python target (Go) — design vs validated Python slice
│  └─ README.md
├─ scripts/
│  ├─ demo.py                          One-command narrated demo (all scenarios + monitoring)
│  ├─ evaluate.py                      Evaluation scorecard: quality / adherence / efficiency; re-tests every recorded live run
│  ├─ verify_change.py                 Re-verify a recorded brownfield change set against its repository
│  ├─ apply_change.py                  Apply an accepted run to a branch (used by the pipeline's acceptance job)
│  ├─ pipeline_request.py              Validate a pipeline request (issue form / Run workflow); format the issue report
│  └─ snapshot_run.py                  Copy a run into examples/ as a committed record
├─ .github/workflows/ci.yml           CI: tests on Linux+Windows, py3.10/3.12; e2e runs; Docker smoke test
├─ .github/workflows/agent.yml        The agent as a team pipeline: request → approval → run → acceptance → pull request
├─ .github/ISSUE_TEMPLATE/agent-request.yml   The "Agent request" form: preset test cases or a custom requirement
├─ src/agentic_sdlc/
│  ├─ __init__.py                     Public API (run_pipeline, models)
│  ├─ __main__.py                     Enables `python -m agentic_sdlc`
│  ├─ cli.py                          Argument parsing + human-readable report
│  ├─ models.py                       Typed contracts shared by all stages (incl. TaskGraph DAG)
│  ├─ prompts/                        Stage system prompts (analyze/decompose/design/codegen/codegen_repair/codegen_change .md) — versioned context
│  ├─ llm/
│  │  ├─ base.py                      ReasoningProvider interface (the swappable brain)
│  │  ├─ client.py                    LLM clients (Claude/OpenAI/Azure) + usage metrics
│  │  ├─ llm_provider.py              LLM-first provider: retries, JSON validation, fallback
│  │  ├─ deterministic.py             Offline engine: classify, decompose, dispatch to packs
│  │  └─ __init__.py                  get_provider() factory (auto-selects the backend)
│  ├─ knowledge/
│  │  ├─ base.py                      KnowledgePack interface
│  │  ├─ url_shortener.py             The mandatory use case: full generated service + tests
│  │  └─ __init__.py                  Generic fallback pack
│  ├─ agents/
│  │  ├─ base.py                      Agent ABC — perceive → decide → act loop + rationale
│  │  ├─ requirement_analyst.py       Understand & normalize; record assumptions
│  │  ├─ task_decomposer.py           Build & validate the task DAG
│  │  ├─ architect.py                 Design + decides persistence default from NFRs
│  │  ├─ codebase_analyst.py          Brownfield impact; snapshots the repo (read-only) → change mode
│  │  ├─ generators.py                Code / Test / Doc generation agents
│  │  ├─ validator.py                 Compile + run tests + contract/doc checks + risks
│  │  ├─ repair.py                    Feedback-loop agent: fixes repairable findings
│  │  ├─ summary.py                   Final engineering summary (Markdown + object)
│  │  └─ __init__.py                  DAG_AGENTS registry (category → agent)
│  ├─ orchestrator/
│  │  ├─ state.py                     Blackboard (shared state) + AgentContext
│  │  ├─ orchestrator.py              Phases, DAG execution, retry/degrade/halt, gates
│  │  └─ __init__.py                  Public orchestration exports
│  ├─ hitl/
│  │  ├─ approval.py                  ApprovalGate: AutoApprove & ConsoleApproval
│  │  └─ __init__.py
│  └─ tools/
│     ├─ filesystem.py                ArtifactStore — sandboxed writes (path-traversal guard)
│     ├─ repo.py                      Brownfield: read-only repo snapshot, relevance ranking, overlay, unified diff
│     ├─ code_runner.py               CodeRunner — in-memory compile + isolated (python -I) unittest subprocess, credential-scrubbed env
│     ├─ static_check.py              AST safety scan: dangerous calls, non-stdlib imports, hard-coded secrets
│     └─ __init__.py                  ToolBox bundle handed to agents
└─ tests/
   ├─ test_models.py                  DAG ordering / cycle & dangling-dep guards
   ├─ test_provider.py                Classification & plan-shape correctness
   ├─ test_tools.py                   Sandbox guard, compilation detection, static safety scan
   ├─ test_llm_provider.py            LLM path with a fake client: parsing, fallback, sandbox gate, repair pass, pricing, hardening
   ├─ test_change_mode.py             Brownfield change sets: accept, repair pass, fallback, repo never written
   └─ test_orchestrator.py            Full runs, recovery, degrade, human-halt, compile-failure halt, scan-before-execute
```

---

## 7. Testing approach

Correctness and output quality are validated at **four** levels:

1. **Framework tests** (`tests/`, 75 cases, `unittest`): classification accuracy, DAG
   topology + cycle/dangling guards, artifact-sandbox enforcement, compilation detection,
   the **LLM provider** (mock-driven: JSON parsing, metrics, per-stage fallback, and
   **code-generation accept + sandbox-validated fallback**), always-on **run metrics**, and
   full orchestrator runs including **retry recovery**, **optional-task degradation**,
   **required-task halt**, **human rejection**, the **validation feedback loop**, and
   **concurrent level execution** (parallel ≡ sequential output; sibling completes before a halt),
   **idempotent agents under a fine-grained plan** (one design, 15 unique artifacts, all 14
   tasks complete), the **LLM spend circuit breaker**, **secret scrubbing** checked from inside a
   real generated-test subprocess, **compile failures halting** even across retries, code with
   a high-severity scan finding **never being executed**, truncation not being retried, and
   plan invariants (validation must depend on the work it reports on).
2. **Generated-code tests** (emitted into every run; for the offline template: unit tests (base62 round-trip, service
   rules, both storage backends) and an **integration test** driving the WSGI app end to end
   (shorten → 302 redirect → stats).
3. **In-pipeline validation** (`Validator`): the system compiles and executes the code it just
   wrote — a run only reports `PASS` when the generated tests actually pass.

```powershell
$env:PYTHONPATH = "src"; python -m unittest discover -s tests -v     # -> Ran 75 tests ... OK
```

4. **Continuous integration** (`.github/workflows/ci.yml`): every push to main and every pull request runs the framework
   and demo suites on Linux and Windows (Python 3.10 and 3.12), executes the mandatory
   use case and every recovery scenario end to end, and builds + smoke-tests the demo
   container (shorten → 302 → stats).

---

## 8. Risks, trade-offs & validation

| Risk / trade-off | Mitigation in the system |
| --- | --- |
| Generated code could be wrong | Validator compiles + runs tests before acceptance |
| Generated code could be **unsafe or hygienically bad** (eval/exec/shell=True, pickle, third-party or outdated packages, hard-coded keys, a file shadowing a stdlib module) | `tools/static_check.py` AST scan (import aliases resolved) runs **before anything is executed** — in the codegen sandbox and in the Validator; a high-severity finding means the code is not run and validation fails |
| An agent step fails transiently | Retry-with-backoff, then degrade (optional) or halt (required) |
| A bad/hostile artifact path | `ArtifactStore` rejects any path escaping the sandbox |
| A runaway generated test hangs the run | Test subprocess has a hard timeout |
| Over-trusting autonomy | Pipeline: two named approvals (spend before the run, acceptance after it). CLI: three in-run gates (clarify, plan, accept), human with `--interactive`. Rejection halts and saves state; the interactive gate **fails closed** (no input = reject); auto mode **never accepts a failing report** at the final gate |
| Ambiguous input yields a false "done" | Ambiguity is surfaced as explicit default assumptions at the clarification gate (a human answers them in `--interactive`); the repair loop only fixes missing artifacts, never failing tests |
| **LLM-authored code may not run** | Provider compiles + runs the generated tests in a sandbox; on rejection the sandbox output is fed back to the model for one repair pass; accepts only on pass, else falls back to the verified template |
| **In-memory store is non-durable** | SQLite backend provided and unit-tested; `SHORTENER_STORE=sqlite` + `SHORTENER_DB_PATH` select it (`demo/url_shortener/server.py`) |
| **Predictable sequential codes** | Documented; hashing/random slugs noted as the trade-off |
| **Synchronous click recording** | Documented; async event pipeline is the scaling path |

The validation *strategy* is layered: **AST safety scan** first (dangerous calls, non-stdlib
imports, hard-coded secrets, stdlib shadowing — nothing runs if it fails) → compile → execute
the tests in an isolated interpreter → contract/doc presence (existence, not conformance) →
risks derived from the produced code → human acceptance gate.

### Fault tolerance — what is enforced

| Failure | Behaviour | Where |
| --- | --- | --- |
| Model call errors / times out / drops the connection / returns bad JSON | bounded retries (failed attempts' tokens are still counted), each attempt, retry, repair pass and fallback **printed on the console as it happens** (`[LLM] …` lines), then **that stage** falls back to the deterministic engine; the run continues and the fallback is recorded in `metrics.llm`. Generated projects and change sets use a delimiter format (`<<<FILE path>>>`) instead of JSON, so whole source files never need escaping | `llm_provider._ask`, `_with_fallback`, `_parse_file_blocks` |
| Model-authored code fails the scan, compile or its own tests | rejected in a throwaway sandbox; the failure output is fed back to the model for **one repair pass** (recorded as a `codegen` retry event — `metrics.llm.retries`); a second rejection uses the verified template, recorded as a `codegen` fallback | `llm_provider._ensure_bundle`, `_llm_repair_files` |
| Model plan is invalid (unknown category, missing code/tests/validate/summary, cycle) | rejected; deterministic plan used. A valid plan is normalized so every validate task depends on all the work it reports on, and summary on validation | `llm_provider._enforce_plan_invariants`, `TaskGraph.validate_acyclic` |
| Generated code does not compile | the Validator raises; the retry re-validates (a failed report is never reused) and the run **halts** for a human with the partial record saved | `ValidatorAgent.act` |
| Model plan is fine-grained (many `code`/`design` tasks) | agents **perceive** existing work and **decide to reuse** it — one design, one bundle, no duplicate artifacts; every task still completes | `Architect/CodeGenerator/... .decide`, `Blackboard.merge` |
| An agent task raises | retry with backoff → optional task **degrades** (logged, skipped) / required task **halts** cleanly with the partial run saved | `orchestrator._run_task` |
| A task fails inside a parallel level | siblings run to completion, then the level halts once | `orchestrator._execute` |
| Generated tests hang | subprocess hard timeout | `CodeRunner.run_unittests` |
| Validation finds a repairable gap | Repair agent fixes it, re-validation runs (bounded iterations) | `orchestrator._repair_loop` |
| LLM spend runs away (runaway plan, retry storm, hostile prompt) | **circuit breaker**, not a budget: `AGENTIC_LLM_MAX_CALLS` (default 200) and `AGENTIC_LLM_MAX_COST_USD` (default 10.00) sit far above a legitimate run (4–8 calls, well under a dollar); once tripped, remaining stages degrade to deterministic and the record says so | `llm_provider._budget_check` |
| Model output hits a ceiling | per-stage output ceilings are set at the model's real capacity (a ceiling costs nothing) and stream on the Anthropic client; truncation is detected, its tokens counted, and **not** retried at the same ceiling (it would truncate again) — the stage falls back; a model that rejects a ceiling as too large is retried with a smaller one | `llm_provider._MAX_TOKENS`, `client.TruncatedOutput`, `client._call_with_ceiling` |
| A model or SDK rejects a parameter (`temperature`, `max_tokens` vs `max_completion_tokens`) | the parameter is dropped or renamed and the call retried, instead of failing the stage | `client._call` |

Try them: `--inject-fault code:1` (retry), `--inject-fault docs:9` (degrade),
`--inject-fault code:9` (halt), `examples/ambiguous.txt` (repair loop).

### AI-specific risks — hallucination, drift, overreach

| Risk | Control in this system | Honest gap |
| --- | --- | --- |
| **Hallucinated code** (invented APIs, imports, behaviour) | Model-authored code is written to a throwaway sandbox, **scanned, compiled, and its own tests executed** before it is accepted; failure → the sandbox output is returned to the model as a repair brief (one pass); second failure → verified template, recorded as a `codegen` fallback | The same model writes the code and its tests, so the tests verify the behaviour it chose to test, not the requirement itself (§9) |
| **Hallucinated plan / design** | Plan: schema-checked JSON, task-category allow-list, required stages, validation made to depend on the work, acyclic check, else deterministic plan. Design: typed parsing. Analysis: every ambiguity becomes an *explicit default assumption* a human sees at the clarification gate, and the **same FRs/NFRs/assumptions are passed to the design and codegen stages** so later stages cannot silently re-open them | — |
| **Design promises more than the code delivers** | The design prompt pins the **implementation target** (Python standard library, single process, in-memory/SQLite) so the model cannot decide on a stack the slice will not implement — production evolutions go to trade-offs, phrased as prototype-vs-production; the summary computes **design ↔ implementation coverage** (which designed endpoints the generated slice actually serves), the repaired API contract documents only implemented endpoints, standing **risks are derived from the produced slice** (its persistence, its auth) rather than copied from the design, a requirement naming a non-Python target gets an explicit limitation line, and every live summary states that individual design decisions are *not* verified against the code | The model's design decisions (e.g. "async queue", "required Idempotency-Key header") are not all implemented by its code; only endpoints, compilation, the scan and the model's own tests are verified. A Critic agent (semantic design↔code↔tests review) is the next step |
| **Drift within a run** (scope creep, loops) | One output schema per agent; agents cannot add tasks; the DAG bounds the work; `reuse` decisions prevent repeated work; bounded repair iterations; call + cost circuit breaker | — |
| **Drift over time** (model / prompt changes) | The deterministic suite is a fixed regression baseline; recorded live runs in `examples/llm-run*` are golden snapshots; `scripts/evaluate.py` scores every scenario and runs in CI on every push | Live runs are not re-executed in CI (cost, non-determinism) — they are scored from their recorded `result.json` and their code is re-tested |
| **Overreach** (an agent doing more than allowed) | Least privilege by construction: an agent's only tools are a sandboxed file store and a subprocess runner — no shell, no network tool, no git, no deploy. Agents never call each other or the model's tools; the model returns data, Python decides. Spend and acceptance approvals (pipeline); three gates with `--interactive` (CLI) | The sandbox is process-level, not network-isolated (see Security below) |
| **Fail-closed by default** | No key → deterministic; bad reply → per-stage fallback; compile failure → halt for a human; any failing check → `REVIEW NEEDED`, never `PASS`, and auto mode does not accept it; no console input → gate rejects; partial runs always persisted | — |

**Memory.** Working memory is the `Blackboard` — one shared, lock-guarded object per run and
the single source of truth for every agent. Agents are stateless between runs, and every model
call is stateless: each stage receives only the blackboard fields it needs, never an accumulating
conversation, so context cannot bloat or drift across stages and nothing leaks between
requirements. `result.json` is the durable *audit record*, not memory. A long-term memory
(repository index for brownfield, retrieval of past runs) is a future seam behind
`CodebaseAnalyst`.

**Why no MCP / model tool-calling.** The model never invokes tools; it returns structured data
(JSON for analysis, plans and designs; file blocks for code) and the orchestrator acts on it. There is no tool-call path; the remaining path is model-authored code executed in the validation sandbox (see Security). MCP
becomes the right choice when the agent must reach Jira, GitHub or a repository in production —
through the firm's gateway, with allow-listed servers.

### Evaluation

`python scripts/evaluate.py` produces a scorecard (also `--json`), and CI runs it on every push to main and every pull request:

| Axis | What is scored |
| --- | --- |
| Output quality | validation checks passed; artifact set has no duplicates; summary carries risks and a validation approach |
| Task adherence | outcome matches the scenario's expectation (pass / halt); every planned task completed or explicitly reused; expected retries / repairs / degradations observed; brownfield impact analysed |
| Tool correctness | the compile + test gate ran (validation result present); sandbox path guard, timeout and secret scrubbing are unit-tested in `tests/test_tools.py` and `tests/test_llm_provider.py` |
| Operational efficiency | duration, tasks, retries, repairs, degradations, parallel levels, reused tasks; for recorded live runs: calls, tokens, estimated cost (recomputed from the recorded tokens with the current price table; `n/a` if the model's price is unknown), fallback stages, and whether the code was model-authored |

Six offline scenarios are scored deterministically (greenfield, brownfield, ambiguous → repair,
retry recovery, optional-task degradation, required-task halt). Recorded live-model runs under
`examples/llm-run*` are scored from their `result.json` and their code is re-tested, so the
model path is evaluated without a key and without non-determinism in CI.

### Security — what is enforced, and what is not

Enforced:

- **Secrets** are read only from environment variables, never written to disk, logs,
  `result.json`, or run snapshots (`scripts/snapshot_run.py` scrubs key-shaped strings
  defensively). `.gitignore` excludes `.env`, `*.key`, `secrets.*`.
- **Generated and model-authored code does not inherit credentials**: the test subprocess
  runs in isolated mode (`python -I -B`) with every `*KEY*`, `*TOKEN*`, `*SECRET*`,
  `*PASSWORD*`, `*CREDENTIAL*` variable removed from its environment
  (`CodeRunner.scrubbed_env`; a test proves it from inside a real subprocess), and only
  after the static scan passed.
- **Artifact writes are confined** to the run's sandbox directory; any path that resolves
  outside it (e.g. `../../etc/passwd`, absolute paths) is rejected (`ArtifactStore`).
- **Untrusted model output is treated as data**: lenient JSON parsing with schema-shaped
  validation, an allow-list of task categories, and the scan+compile+test gate before any
  model-authored code is accepted. Prompt-injected instructions can still produce code that
  passes the scan and runs in the sandbox, which is not network-isolated (see below).
- **Bounded execution**: per-call timeouts, bounded retries, bounded repair iterations,
  bounded spend.
- **Human approval**: in the pipeline, GitHub Environment approvals before the run (spend)
  and after it (acceptance); locally, gates at clarification, plan and acceptance with
  `--interactive`. A rejection halts.
- **The model key in the pipeline** exists only in one short step, which writes it to a private
  file; the agent reads and deletes that file at start-up, so the key is never in the agent's
  initial environment — the part `/proc/<pid>/environ` exposes to child processes such as the
  model-written tests it executes (a test proves this). The static scan also rejects code that
  names `/proc/`. The checkout keeps no token, and the job that holds a write token never
  executes generated code.
- **Container**: the demo image runs as a non-root user with a health check.

Not enforced in this prototype (documented, would be required for production):

- The validation sandbox is a temp directory + isolated-mode subprocess with a timeout and
  a scrubbed environment — **not** a network-isolated container or a separate OS user.
  Model-authored tests can still reach the network and the host filesystem within the
  process's permissions. The key is kept out of their reach as described above (it is not in
  any environment they can read), but they run as the same user with network access on the
  runner, so the `agent-run` approver reads the requirement before approving, and the key is
  dedicated to the pipeline with a spend limit. The static scan (which runs first) is a
  guardrail, not a sandbox. In
  production this step runs in an ephemeral, no-network container under a separate user
  (gVisor/Firecracker), and the key comes from a secret store.
- The deterministic URL shortener is an **open redirect by design** (any `http(s)` target),
  has **no authentication**, rate limiting only when a requirement asks for it, and uses
  **predictable sequential codes**; these are stated in its trade-offs and risks.

---

## 9. Assumptions & limitations

**Assumptions** (also recorded per-run in `ENGINEERING_SUMMARY.md`):
- Where a requirement is ambiguous, the system proceeds on documented default assumptions
  unless a human intervenes at the clarification gate.
- The generated service favours the standard library and clarity over framework features.

**Limitations:**
- Code and test generation is **model-authored and sandbox-validated**: the LLM writes the
  project from the requirement and it is accepted only if it passes the static scan,
  compiles and its own tests pass; a rejected bundle gets one repair pass with the sandbox
  output, and only then is a verified template used. The default/offline (no-key) path
  always uses the verified template.
- On the live path the model is asked for the code, `openapi.yaml`, README and tests as one
  bundle (at most 12 files, so it fits one response). If it omits the contract or README, the
  Repair agent synthesizes them from the design, listing only endpoints found in the code.
  The validator checks that a contract exists, not that it conforms to the code — e.g. the
  rate-limit change in [PR #5](https://github.com/ummarvali/AgenticSoftware/pull/5) returns 429 but left `openapi.yaml` unchanged. Change-mode
  validation now reports whether the change set updated the contract, and the change prompt
  requires it whenever HTTP behaviour changes.
- **Design decisions are not verified individually.** A model's design can describe more
  than its slice implements (an async queue, a required header, role checks); the coverage
  table verifies endpoints, and the tests verify behaviour the model chose to test. Each
  live summary says so. A Critic agent is the next step.
- **Security semantics are not verified by the gates.** The scan rejects dangerous calls and
  the tests prove behaviour, but neither judges whether a design is safe: the brownfield
  pipeline run ([PR #3](https://github.com/ummarvali/AgenticSoftware/pull/3)) passed 6/6 and still exposed an unauthenticated admin endpoint
  and trusted a client-chosen header as its rate-limit key — found at code review. The
  code-generation prompts now carry explicit security rules, and the re-run
  ([PR #5](https://github.com/ummarvali/AgenticSoftware/pull/5)) closed both gaps; a security-review agent before
  the acceptance gate is the next step, and human review of the pull request stays mandatory.
- **The task graph is for traceability more than execution granularity.** With a live model
  each stage is one model call (analysis, plan, design, code+tests), and most planned tasks
  are logged as `reuse` of that output (the CLI URL-shortener run: 23 of 29) — the plan shows
  what was covered and why, not 29 separate generations.
- **Ambiguity in the pipeline is resolved by assumption, not by conversation.** Locally the
  clarification gate (`--interactive`) lets a human confirm the assumptions before anything
  is built; in the pipeline they are only seen afterwards, in the result. For "Make the app
  faster." ([PR #7](https://github.com/ummarvali/AgenticSoftware/pull/7)) that produced a broader change than a reviewer would likely
  want. The next step is a clarification round on the issue: post the assumptions, wait for
  the requester to confirm or correct them, then build.
- **"Scalable" is designed, not load-tested.** The scaling path (sharded store, cache,
  async analytics, stateless replicas) is in each design and its trade-offs; the generated
  slice is a single process and no load test is run.
- **Pipeline pull requests are not re-tested by CI**: pull requests opened with the default
  `GITHUB_TOKEN` do not trigger workflows. Their code was compiled, scanned and tested in the
  agent run; a GitHub App token would make CI run on them too.
- Generated code is validated on the platform the agent runs on. The recorded services were
  generated and gate-validated on Linux; CI re-tests them on Linux, and on Windows the
  scorecard reports that re-test as skipped (generated code is not guaranteed to be portable —
  some of it assumes POSIX file semantics). The agent itself is tested on Linux and Windows.
- The model's own tests are the behavioural evidence; they cover main paths, not edge cases
  (e.g. the recorded shortener accepts a TTL but its cache path does not re-check expiry).
- Human checkpoints: console prompts when run locally; in the GitHub pipeline, named approvals through GitHub Environments (spend before a run, acceptance after it). There is no dedicated web UI.
- Brownfield change mode: the repo scan ranks files by term overlap with the requirement (a
  heuristic, not a static-analysis/impact engine); the model sees a relevance-ranked subset
  of the repository (~60 KB), so a change spanning a large codebase may miss context;
  file deletions are not proposed; the overlay runs the repository's `unittest` suite under
  `tests/` (other runners — pytest, go test — need a runner adapter). Offline, only the
  rate-limit change on the demo layout is authored.

---

## 10. Requirement-coverage matrix

| Assignment requirement | Where it lives |
| --- | --- |
| Requirement understanding + ambiguities | `agents/requirement_analyst.py`, `llm/deterministic.py` |
| Normalize into an engineering problem | `AnalysisResult.normalized_problem` |
| Task decomposition + dependencies | `agents/task_decomposer.py`, `models.TaskGraph` |
| Execution sequence (DAG levels) | `TaskGraph.topological_levels`, `orchestrator._execute` |
| Codebase reasoning (brownfield) | `agents/codebase_analyst.py`, `tools/repo.py` (snapshot, relevance ranking, overlay, diff) |
| Brownfield changes (enhancement / bug fix / refactor / tests / docs) | change mode: `CodeGenerator._propose_change`, `LLMProvider.generate_change` + `prompts/codegen_change.md`, overlay validation in `ValidatorAgent` |
| Multi-step orchestration + cross-step coordination | `orchestrator.py` + `Blackboard` |
| Error handling & recovery | `orchestrator._run_task` (retry / degrade / halt) + `_repair_loop` (validation feedback) |
| Agent autonomy (perceive → decide → act) | `agents/base.py`, `decision` events per agent |
| Code / API contract / tests / docs | LLM-authored + sandbox-gated (`llm/llm_provider.py`); verified template (`knowledge/url_shortener.py`) |
| Validation & guardrails | `agents/validator.py` (5 checks greenfield, 6 in change mode, incl. AST safety scan), `tools/*`, sandbox + timeout |
| Controlled autonomy (human oversight) | `hitl/approval.py`, three gates in `orchestrator.py`; `agent.yml` environments `agent-run` / `agent-acceptance` |
| Final structured engineering summary (plan as executed, rationale + decision log, artifacts, **validation approach + per-check results**, run monitoring, risks, trade-offs, assumptions, limitations) | `agents/summary.py`, `ENGINEERING_SUMMARY.md`, `result.json` |
| LLM reasoning + reliability fallback | `llm/llm_provider.py`, `llm/client.py` |
| Observability (tokens / cost / latency) | `llm/client.py::MetricsCollector`, `result.json` metrics |
| Mandatory URL-shortener use case | `knowledge/url_shortener.py` (generated & tested); `demo/` + `Dockerfile` |
| Concurrent execution of independent tasks | `orchestrator._execute` (thread per task per DAG level), `Blackboard._lock` |
| Evidence of the model-driven path | pipeline runs → [PR #1](https://github.com/ummarvali/AgenticSoftware/pull/1), [PR #3](https://github.com/ummarvali/AgenticSoftware/pull/3), [PR #5](https://github.com/ummarvali/AgenticSoftware/pull/5), [PR #7](https://github.com/ummarvali/AgenticSoftware/pull/7); `examples/llm-run*/result.json` — three greenfield domains and one brownfield change set (tokens, latency, cost, retries, per-stage fallbacks). The codegen repair pass is exercised by `tests/test_llm_provider.py` and, when a model bundle is rejected, recorded in `metrics.llm.calls` |
| Reproducibility / CI | `.github/workflows/ci.yml` — Linux + Windows, e2e scenarios, Docker smoke test |
| Controlled autonomy in a team setting | `.github/workflows/agent.yml` — request via issue form, spend approval, agent run in CI, acceptance approval, PR on acceptance |
| Spend circuit breaker (abuse guard, not a budget) | `llm_provider._budget_check` — `AGENTIC_LLM_MAX_CALLS` / `AGENTIC_LLM_MAX_COST_USD` |
| Evaluation (quality / adherence / tool correctness / efficiency) | `scripts/evaluate.py` (CI step), `tests/test_tools.py` |
| AI-risk controls (hallucination / drift / overreach) | sandbox gate, schema + allow-list, bounded loops, least-privilege tools, human gates — §8 |
| Secret isolation for generated code | `tools/code_runner.scrubbed_env` |
| Idempotent agents (no duplicate work under rich plans) | `agents/*.decide` → `reuse`; `Blackboard.merge` |
| Design ↔ implementation coherence | `_analysis_context` (shared stage context), `Blackboard.endpoint_coverage`, summary coverage table + scope limitations |

---

## 11. Extending the system

- **New domain:** add a `KnowledgePack` (architecture/code/tests/docs) and register it in
  `DeterministicProvider._PACKS`. No agent or orchestrator change required.
- **New capability:** add an `Agent`, one entry in `agents.DAG_AGENTS`, and a task in the
  decomposer with the matching `category`.
- **Prompts are files, not code:** each stage's system prompt lives in
  `src/agentic_sdlc/prompts/<stage>.md` (analyze, decompose, design, codegen, codegen_repair, codegen_change). Edit and diff
  them like any versioned context; point `AGENTIC_PROMPTS_DIR` at a folder to A/B a prompt set
  without touching Python.
- **Model temperature:** `AGENTIC_LLM_TEMPERATURE` sets it explicitly (OpenAI-compatible
  backends default to `0.2`; Anthropic uses the model default unless set). If the installed
  SDK or the selected model rejects the parameter, the call is retried without it rather
  than failing the stage — a sampling knob must never take a run down.
- **Live LLM:** `pip install -e ".[anthropic]"` (or `".[llm]"`), set `ANTHROPIC_API_KEY`
  (or `OPENAI_API_KEY` / Azure vars), run with `--provider claude` (or `openai`).

---

## 12. Operating this in production — the SRE view

The agent system is the application; a pipeline is how it is run and operated by a team.
**That pipeline is implemented and exercised in this repository** (example: [run](https://github.com/ummarvali/AgenticSoftware/actions/runs/36131504163) → [pull request #1](https://github.com/ummarvali/AgenticSoftware/pull/1)):
[`.github/workflows/agent.yml`](.github/workflows/agent.yml).

```
Request: an issue from the "Agent request" form (preset test cases or your own requirement)
         or Actions → "Agent pipeline" → Run workflow (maintainers)
   │
   ├─ request     untrusted text parsed as data: preset scenario, length cap, allow-listed folder
   │
   ├─ ⏸ approval  GitHub Environment "agent-run": a maintainer approves spend before the key is used
   │
   ├─ agent run   key from the environment secret (this job only, main branch only) · live [LLM] console trail
   │                         engineering summary published on the run page · run record uploaded
   │                         a failing or halted run, a missing key, or any model stage that fell
   │                         back to the deterministic engine stops here — never offered for approval
   │
   ├─ report      verdict, checks, tokens/cost and the engineering summary posted on the issue
   │
   ├─ ⏸ approval  GitHub Environment "agent-acceptance": a reviewer accepts or rejects the result
   │
   └─ pull request  accepted change set applied to a branch (scripts/apply_change.py, line
                    endings preserved) → PR with the summary as its description, linked on the issue
```

**Try it (reviewers).** Open an issue → [*Agent request*](https://github.com/ummarvali/AgenticSoftware/issues/new?template=agent-request.yml) → pick a test case — the mandatory URL
shortener, another greenfield domain, a brownfield change to `demo/`, the ambiguous "Make the
app faster.", or your own requirement — and submit. The issue gets a link to the run; once a
maintainer approves the spend, the console streams live and the result is posted back on the
issue, followed by the pull request if it is accepted. No key or write access is needed.
To run it with your own key and approvals instead, fork it — [TL;DR B](#b-run-it-in-your-own-fork--your-key-your-approvals).

Two environments, because they gate two different decisions: `agent-run` is *who may spend
model credit* (and it holds the key), `agent-acceptance` is *whether the output is good
enough to become a pull request*. GitHub approves a job before it starts, so the sign-off on
a result needs its own job after the run.

What this changes compared with running the CLI on a laptop: nobody holds the model key (it
lives in the secret store and reaches one step of one job); every run is an auditable CI run
with its console log, summary and `result.json` retained; acceptance is a recorded approval
by a named reviewer; the output enters the normal review path as a pull request; and spend is
bounded by the approval on `agent-run` plus the workspace spend limit. Requirement text is
parsed by `scripts/pipeline_request.py` and passed to the agent as an environment variable,
never interpolated into a script, so it cannot inject shell commands.

**Who can use the key (public repository).** GitHub secrets are encrypted and write-only —
nobody, including the owner, can read one back, and they are masked in logs. The workflow has
no `pull_request` trigger, so forks cannot run it with the key. Anyone can open an *Agent
request* issue, but that run waits for a required reviewer of `agent-run` before the key is
released. The key is an *environment* secret of `agent-run`, restricted to `main`, so a
workflow edited on another branch cannot reach it. Outside the repository: a dedicated key
for this pipeline, a monthly spend limit on its Anthropic workspace, and rotation on a schedule.

One-time setup (repository Settings): create the `agent-run` environment (required reviewers —
mandatory, since issues can start runs; deployment branches: `main` only) and add
`ANTHROPIC_API_KEY` as its environment secret; create the
`agent-acceptance` environment with required reviewers; allow GitHub Actions to create pull
requests (Actions → General → Workflow permissions). Pull requests opened with the default
`GITHUB_TOKEN` do not trigger other workflows — a GitHub App token would let CI run on them
automatically.

- **Hosting & triggers:** here the job runs on a GitHub-hosted runner; the root `Dockerfile`
  packages the same agent for Argo Workflows / a Kubernetes Job, triggered by a ticket, a PR
  comment or an API call; any approval system plugs in through the `ApprovalGate` seam.
- **SLOs for the agent itself:** run success rate, validation pass rate, LLM fallback rate,
  p95 run duration, cost per run — all already emitted in `result.json` (`metrics.run`,
  `metrics.llm`) and ready to ship to Prometheus/OpenTelemetry.
- **Alerting:** a fallback-rate spike means the model or its gateway is degrading; a
  validation-pass-rate drop means prompts or templates regressed; cost-cap hits mean a plan
  blew up. Each maps to a runbook.
- **Isolation:** the validation sandbox moves into an ephemeral, no-network container
  (gVisor/Firecracker); the model is reached through the firm's gateway with per-team
  quotas; secrets come from the platform's secret store, never the environment.
- **Rollout of prompt/template changes:** prompts are versioned files, so a change is a
  reviewable diff. Canary it: run the new prompt set (`AGENTIC_PROMPTS_DIR`) against the
  scenarios in `examples/` with `scripts/evaluate.py`, compare validation and fallback
  metrics, then promote.

---

**License:** MIT. This prototype is intended as production-*shaped* reference work: modular,
typed, tested and documented, with its gaps written down.
