# Agentic SDLC — Requirement → Reviewable Engineering Outcome

An **agentic software-engineering system** that takes a plain-language requirement and
drives it across the SDLC — understand → decompose → orchestrate → generate → **validate** —
producing production-shaped code, an API contract, tests, docs, and a structured
engineering summary, all under **controlled autonomy** (agents act, humans approve).

> Built for the mandatory use case: *"Build a scalable URL shortener service with APIs,
> persistence, and analytics."* — and it also handles greenfield, brownfield, and
> ambiguous requirements generally.

- **LLM-first, never LLM-dependent** — the model (Claude / OpenAI / Azure) drives the
  reasoning; the system degrades to a deterministic engine on any error, with retries,
  timeouts, and token/cost/latency **observability**. The default needs no key.
- **The system verifies its own output** — generated code is compiled and its tests are
  executed before a human is asked to accept the run.

---

## ⚡ TL;DR — run the URL shortener now (no agent needed)

The runnable deliverable is committed under [`demo/`](demo/). After cloning:

```bash
cd demo
python -m url_shortener.server            # serves http://127.0.0.1:8000
# in another shell:
curl -X POST http://127.0.0.1:8000/api/shorten -H "Content-Type: application/json" -d "{\"url\": \"https://example.com/a/very/long/path\"}"
curl -i http://127.0.0.1:8000/<code>          # 302 redirect
curl http://127.0.0.1:8000/api/stats/<code>   # click analytics

python -m unittest discover -s tests -v   # 20 tests, all pass
```

`demo/` is the **exact output the agent generated** for the mandatory requirement — checked
in so a reviewer can run the app immediately. To watch the agent *produce* it from scratch,
see [§2](#2-quick-start-setup-instructions).

---

## Design philosophy — LLM-first, with a reliability fallback

The reasoning layer is a swappable seam (`ReasoningProvider`), so the *same* agents and
orchestration run on any brain:

- **LLM backend (Claude / OpenAI / Azure OpenAI / any OpenAI-compatible endpoint)** — the
  model drives requirement **analysis, task decomposition, and architecture design**. It is
  used automatically when a key is present (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
  `AZURE_OPENAI_ENDPOINT`, or `OPENAI_BASE_URL`) — no code change.
- **Deterministic engine (default when no key, and the fallback for every LLM stage)** — a
  reproducible rule engine that **backs each model call**: on a timeout, error, or malformed
  response the pipeline degrades to it per-stage, so a run never half-completes.

Every model call is wrapped with a **timeout, bounded retries, strict JSON validation**, and
**per-stage fallback**. **Every run** (LLM or offline) emits **monitoring** — wall-clock
duration, task count, retries, repairs, degradations, and gate decisions — plus LLM tokens,
latency, cost, and fallback count when a model is used (printed and saved in `result.json`).
Failures can be **injected on demand** (`--inject-fault code:1`) to demonstrate recovery.

This is the SRE stance made concrete: an agent that is **LLM-first but never LLM-dependent**.
For **generation**, the model authors the whole project *from the requirement*; the provider
then **compiles it and runs its tests in a throwaway sandbox** and accepts it only if it
passes — otherwise it falls back to a verified template. So generation is genuinely
requirement→code, while the demo stays guaranteed-runnable.

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

**Requirement:** Python 3.10+ (developed on 3.14). No third-party packages are needed for
the core system.

**One-command narrated demo** (all scenarios + fault injection + monitoring):

```powershell
python scripts/demo.py            # add --provider claude to run on a live model
```

Or drive it yourself:

```powershell
# From the project root
cd agentic-sdlc-system

# Option A — run directly (no install), just put src on the path:
$env:PYTHONPATH = "src"
python -m agentic_sdlc "Build a scalable URL shortener service with APIs, persistence, and analytics."

# Option B — install as a package (adds the `agentic-sdlc` command):
python -m pip install -e .
agentic-sdlc "Build a scalable URL shortener service with APIs, persistence, and analytics."
```

> On Windows the interpreter may be `py`, `python`, or a full path such as
> `C:\Users\<you>\.local\bin\python3.14.exe`. Substitute accordingly.

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

```powershell
pip install -e ".[anthropic]"
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # from console.anthropic.com (NOT a claude.ai login)
python -m agentic_sdlc --provider claude "Build a scalable URL shortener service with APIs, persistence, and analytics."
# OpenAI/Azure instead: pip install -e ".[llm]"; set OPENAI_API_KEY (or AZURE_OPENAI_ENDPOINT); --provider openai
```

**CLI flags:** `--file <path>`, `--repo <path>` (brownfield scan), `--interactive`,
`--provider auto|deterministic|claude|openai`, `--inject-fault <category[:N]>`, `--quiet`, `--json`.

Every run writes to `runs/<run-id>/`:
`artifacts/` (the generated project) + `result.json` (full machine-readable record) +
`artifacts/ENGINEERING_SUMMARY.md` (the human-readable deliverable).

---

## 3. The mandatory use case, end to end

```
$ python -m agentic_sdlc "Build a scalable URL shortener service with APIs, persistence, and analytics."

[RequirementAnalyst] kind=greenfield domain=url_shortener (3 ambiguities, 5 FRs)
[TaskDecomposer] 6 tasks, 5 levels (design, code|docs, tests, validate, summary)

[orchestrator] --- level 0: design ---
[Architect] designed 5 components, 4 API endpoints
[orchestrator] --- level 1: code, docs ---        <- independent work in one level
[CodeGenerator] generated 9 files
[DocGenerator] generated 2 documentation files
[orchestrator] --- level 2: tests ---
[TestGenerator] generated 3 test files
[orchestrator] --- level 3: validate ---
[Validator] 4/4 checks passed                      <- code compiled AND tests executed
[orchestrator] --- level 4: summary ---

Classification : greenfield (domain=url_shortener, confidence=0.92)
Artifacts      : 15 files -> runs\...\artifacts
Validation     : 4/4 checks passed (PASS)
```

**What it generated** (a real, runnable service — standard library only):

| Artifact | Purpose |
| --- | --- |
| `url_shortener/base62.py` | id ⇄ slug codec (short, dense, URL-safe codes) |
| `url_shortener/store.py` | `Store` protocol + **InMemory** and **SQLite** backends |
| `url_shortener/service.py` | validation, idempotent shorten, aliases, expiry, resolve, stats |
| `url_shortener/analytics.py` | click aggregation (totals, top referrers) |
| `url_shortener/api.py` | WSGI HTTP adapter (shorten / redirect / stats / health) |
| `url_shortener/server.py` | dev server entrypoint |
| `openapi.yaml` | the API contract |
| `tests/test_*.py` | unit **and** integration tests (run by the validator) |
| `README.md`, `docs/ARCHITECTURE.md`, `ENGINEERING_SUMMARY.md` | documentation |

You can run the generated service directly:

```powershell
cd runs\<run-id>\artifacts
python -m url_shortener.server                       # http://127.0.0.1:8000
python -m unittest discover -s tests -v              # its own tests pass
```

---

## 4. How it works — architecture & control flow

```
  Requirement
       │
       ▼
  Analyst ─► Decomposer ─►  DAG (by dependency level)
  (normalize) (task graph)   ┌──────────────────────────┐
       ▲                     │ Architect ─► CodeGen      │
       │  human gates        │            ╲ DocGen  ╱    │
       │ (clarify·plan·      │             ─► TestGen    │
       │   accept)           └───────────┬──────────────┘
       │                                 ▼
       │                            ┌───────────┐  fail & repairable
       │   every agent reads/       │ Validator │───────────────┐
       │   writes the shared        └─────┬─────┘               ▼
       │        BLACKBOARD ◄───────────── │ pass          ┌──────────┐
       │   (state + decision log)         ▼               │  Repair  │
       └───────────────────────────  SummaryWriter        └────┬─────┘
                                          │   ▲   re-validate   │
                                          ▼   └────────────────┘
                                 artifacts + result.json
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
  dependency *levels* (so `code` and `docs` share a level — real parallelism, not a flat
  list). → **human plan gate**.
- **Execute** — the orchestrator walks the levels and dispatches each task to the agent
  registered for its `category`, all coordinating through the `Blackboard`. Each task is
  wrapped in **retry-with-backoff**; persistent failures **degrade** optional tasks or
  **halt** required ones.
- **Validate → Repair (feedback loop)** — `Validator` compiles every `.py` file and runs the
  generated test suite in a sandboxed subprocess. If it reports a *repairable* finding
  (e.g. a missing API contract), the orchestrator routes control **back** to the `Repair`
  agent, which fixes it, and then **re-validates** — a bounded, agent-driven self-correction,
  not a blind retry. (Watch the ambiguous example heal `3/4 → 4/4`.)
- **Accept** — a final human gate reviews the validation report before `SummaryWriter`
  emits the summary and `result.json`.

**Controlled autonomy** = agents run independently across many steps, but the run pauses at
three gates (`ConsoleApproval` interactively, or `AutoApprove` for scripted/CI use), and any
gate rejection halts cleanly while still saving the partial result.

---

## 5. Example scenarios

See [`examples/README.md`](examples/README.md) for inputs, commands, and expected outputs for
the **greenfield**, **brownfield**, and **ambiguous** cases, plus the error-recovery demos.
Highlights:

- **Greenfield** → full URL-shortener package, `4/4` checks pass.
- **Brownfield** ("...existing...") → an extra `impact` task is injected *before* `code`; the
  summary includes a Codebase-Impact section; `--repo <path>` scans a real repo.
- **Ambiguous** ("Make the app faster.") → surfaces blocking questions with default
  assumptions and correctly returns **REVIEW NEEDED** rather than a false pass.

---

## 6. Project layout — what every file does

```
agentic-sdlc-system/
├─ pyproject.toml                     Package metadata, entry point, pytest config
├─ requirements.txt                   Optional extras only (dev=pytest, llm=openai)
├─ .gitignore                         Excludes caches, venvs, and generated runs/
├─ README.md                          This document
├─ LICENSE                            MIT
├─ demo/                              ★ The generated URL shortener — runnable as-is
│  ├─ url_shortener/                  service package (base62, store, service, api, server)
│  ├─ tests/                          unit + integration tests (20, all pass)
│  ├─ openapi.yaml                    API contract
│  └─ README.md / docs/ / ENGINEERING_SUMMARY.md
├─ docs/ARCHITECTURE.md               Diagrams, control flow, design decisions
├─ examples/                          Three requirement inputs + expected outputs
│  ├─ greenfield.txt / brownfield.txt / ambiguous.txt
│  └─ README.md
├─ scripts/
│  └─ demo.py                          One-command narrated demo (all scenarios + monitoring)
├─ src/agentic_sdlc/
│  ├─ __init__.py                     Public API (run_pipeline, models)
│  ├─ __main__.py                     Enables `python -m agentic_sdlc`
│  ├─ cli.py                          Argument parsing + human-readable report
│  ├─ models.py                       Typed contracts shared by all stages (incl. TaskGraph DAG)
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
│  │  ├─ codebase_analyst.py          Brownfield impact; decides scan-repo vs design-only
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
│     ├─ code_runner.py               CodeRunner — py_compile + unittest subprocess
│     └─ __init__.py                  ToolBox bundle handed to agents
└─ tests/
   ├─ test_models.py                  DAG ordering / cycle & dangling-dep guards
   ├─ test_provider.py                Classification & plan-shape correctness
   ├─ test_tools.py                   Sandbox guard + compilation detection
   └─ test_orchestrator.py            Full runs, recovery, degrade, and human-halt paths
```

---

## 7. Testing approach

Correctness and output quality are validated at **three** levels:

1. **Framework tests** (`tests/`, 28 cases, `unittest`): classification accuracy, DAG
   topology + cycle/dangling guards, artifact-sandbox enforcement, compilation detection,
   the **LLM provider** (mock-driven: JSON parsing, metrics, per-stage fallback, and
   **code-generation accept + sandbox-validated fallback**), always-on **run metrics**, and
   full orchestrator runs including **retry recovery**, **optional-task degradation**,
   **required-task halt**, **human rejection**, and the **validation feedback loop**.
2. **Generated-code tests** (emitted into every run): unit tests (base62 round-trip, service
   rules, both storage backends) and an **integration test** driving the WSGI app end to end
   (shorten → 302 redirect → stats).
3. **In-pipeline validation** (`Validator`): the system compiles and executes the code it just
   wrote — a run only reports `PASS` when the generated tests actually pass.

```powershell
$env:PYTHONPATH = "src"; python -m unittest discover -s tests -v     # -> Ran 28 tests ... OK
```

---

## 8. Risks, trade-offs & validation

| Risk / trade-off | Mitigation in the system |
| --- | --- |
| Generated code could be wrong | Validator compiles + runs tests before acceptance |
| An agent step fails transiently | Retry-with-backoff, then degrade (optional) or halt (required) |
| A bad/hostile artifact path | `ArtifactStore` rejects any path escaping the sandbox |
| A runaway generated test hangs the run | Test subprocess has a hard timeout |
| Over-trusting autonomy | Three human approval gates; rejection halts and saves state |
| Ambiguous input yields a false "done" | Ambiguous runs return **REVIEW NEEDED**, not PASS |
| **LLM-authored code may not run** | Provider compiles + runs the generated tests in a sandbox; accepts only on pass, else falls back to the verified template |
| **In-memory store is non-durable** | SQLite backend provided and unit-tested; selectable via env |
| **Predictable sequential codes** | Documented; hashing/random slugs noted as the trade-off |
| **Synchronous click recording** | Documented; async event pipeline is the scaling path |

The validation *strategy* is layered: static (compile) → dynamic (execute tests) →
contract/doc presence → risk register → human acceptance gate.

---

## 9. Assumptions & limitations

**Assumptions** (also recorded per-run in `ENGINEERING_SUMMARY.md`):
- Where a requirement is ambiguous, the system proceeds on documented default assumptions
  unless a human intervenes at the clarification gate.
- The generated service favours the standard library and clarity over framework features.

**Limitations:**
- Code/test/doc generation is **model-authored and sandbox-validated**: the LLM writes the
  project from the requirement and it is accepted only if it compiles and its tests pass;
  otherwise a verified template is used. The default/offline (no-key) path always uses the
  verified template.
- Human checkpoints are **console-based** in this prototype (no web UI).
- The brownfield repo scan is a summarizing heuristic (candidate touch points), not a full
  static-analysis/impact engine.

---

## 10. Requirement-coverage matrix

| Assignment requirement | Where it lives |
| --- | --- |
| Requirement understanding + ambiguities | `agents/requirement_analyst.py`, `llm/deterministic.py` |
| Normalize into an engineering problem | `AnalysisResult.normalized_problem` |
| Task decomposition + dependencies | `agents/task_decomposer.py`, `models.TaskGraph` |
| Execution sequence (DAG levels) | `TaskGraph.topological_levels`, `orchestrator._execute` |
| Codebase reasoning (brownfield) | `agents/codebase_analyst.py` |
| Multi-step orchestration + cross-step coordination | `orchestrator.py` + `Blackboard` |
| Error handling & recovery | `orchestrator._run_task` (retry / degrade / halt) + `_repair_loop` (validation feedback) |
| Agent autonomy (perceive → decide → act) | `agents/base.py`, `decision` events per agent |
| Code / API contract / tests / docs | LLM-authored + sandbox-gated (`llm/llm_provider.py`); verified template (`knowledge/url_shortener.py`) |
| Validation & guardrails | `agents/validator.py`, `tools/*`, sandbox + timeout |
| Controlled autonomy (human oversight) | `hitl/approval.py`, three gates in `orchestrator.py` |
| Final structured engineering summary | `agents/summary.py`, `ENGINEERING_SUMMARY.md`, `result.json` |
| LLM reasoning + reliability fallback | `llm/llm_provider.py`, `llm/client.py` |
| Observability (tokens / cost / latency) | `llm/client.py::MetricsCollector`, `result.json` metrics |
| Mandatory URL-shortener use case | `knowledge/url_shortener.py` (generated & tested) |

---

## 11. Extending the system

- **New domain:** add a `KnowledgePack` (architecture/code/tests/docs) and register it in
  `DeterministicProvider._PACKS`. No agent or orchestrator change required.
- **New capability:** add an `Agent`, one entry in `agents.DAG_AGENTS`, and a task in the
  decomposer with the matching `category`.
- **Live LLM:** `pip install -e ".[anthropic]"` (or `".[llm]"`), set `ANTHROPIC_API_KEY`
  (or `OPENAI_API_KEY` / Azure vars), run with `--provider claude` (or `openai`).

---

**License:** MIT. This prototype is intended as production-*shaped* reference work: modular,
typed, tested, documented, and defensible end to end.
