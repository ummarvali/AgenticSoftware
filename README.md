# Agentic SDLC — from a requirement to a reviewed pull request

[![CI](https://github.com/ummarvali/AgenticSoftware/actions/workflows/ci.yml/badge.svg)](https://github.com/ummarvali/AgenticSoftware/actions/workflows/ci.yml)

An agentic system that turns a plain-language requirement into a reviewable engineering
outcome: it **analyses** the requirement, **plans** the work as a task graph, **designs** the
architecture, **generates** code, an API contract, tests and docs, and **validates** the result
(compiles it and runs its tests) before a human approves it. It runs as a GitHub Actions
pipeline — a requirement arrives as an issue, the accepted result arrives as a pull request.

Built for the mandatory use case: *"Build a scalable URL shortener service with APIs,
persistence, and analytics."*

---

## 1. Run the URL shortener (1 minute, no key, no installs)

Python 3.10+ only. The service below is committed in [`demo/`](demo/):

```bash
git clone https://github.com/ummarvali/AgenticSoftware
cd AgenticSoftware/demo
SHORTENER_STORE=sqlite python3 -m url_shortener.server     # http://127.0.0.1:8000
# Windows PowerShell:  $env:SHORTENER_STORE="sqlite"; python -m url_shortener.server
```

In a second terminal:

```bash
curl -X POST http://127.0.0.1:8000/api/shorten -H "Content-Type: application/json" -d '{"url": "https://example.com/a/long/path"}'
curl -i http://127.0.0.1:8000/<code>          # 302 redirect to the long URL
curl http://127.0.0.1:8000/api/stats/<code>   # click analytics
```

Its tests: `python3 -m unittest discover -s tests` (20 tests). It has an OpenAPI contract
([`demo/openapi.yaml`](demo/openapi.yaml)), SQLite or in-memory storage, and a container image.

## 2. See the agent build it

The agent runs in GitHub Actions. Anyone can try it — no key or install needed:

1. Open an issue from the **[Agent request form](https://github.com/ummarvali/AgenticSoftware/issues/new?template=agent-request.yml)**,
   write a requirement (the URL shortener is pre-filled) and pick *New project* or *Change to demo/*.
2. The maintainer approves the run (the model key stays in GitHub and is never exposed).
3. The agent analyses the requirement. If something essential is unclear, it **asks on the issue**
   and waits for an `/answer` comment; otherwise it builds straight away.
4. The result — checks, tests, cost and the engineering summary — is posted on the issue.
5. The maintainer accepts it, and a **pull request** opens with the generated code.

```
issue ─▶ ⏸ approve run ─▶ agents analyse ─┬─▶ build + validate ─▶ result on issue ─▶ ⏸ accept ─▶ pull request
                                          └─▶ blocking questions on the issue ─▶ "/answer …" ─▶ build
```

**Recorded runs** (each PR description is the full engineering summary: plan, decisions,
validation, risks, trade-offs):

| What | Where | Result |
| --- | --- | --- |
| **The mandatory requirement**, word for word | [issue #12](https://github.com/ummarvali/AgenticSoftware/issues/12) → [PR #13](https://github.com/ummarvali/AgenticSoftware/pull/13) | 5/5 checks, 25 generated tests; API-key auth, SQLite, analytics, TTL, owner-only delete; ran cleanly under 40 concurrent writes |
| A change to an existing service (rate limiting on `demo/`) | [issue #2](https://github.com/ummarvali/AgenticSoftware/issues/2) → [PR #3](https://github.com/ummarvali/AgenticSoftware/pull/3), then [issue #4](https://github.com/ummarvali/AgenticSoftware/issues/4) → [PR #5](https://github.com/ummarvali/AgenticSoftware/pull/5) | code review of #3 found two security gaps; after a prompt fix, #5 closed both |
| An ambiguous requirement ("Make the app faster.") | [issue #8](https://github.com/ummarvali/AgenticSoftware/issues/8) → [PR #9](https://github.com/ummarvali/AgenticSoftware/pull/9) | the agent asked first, then built a focused cache + delete change matching the answers |
| An earlier run of the mandatory requirement | [issue #10](https://github.com/ummarvali/AgenticSoftware/issues/10) → [PR #11](https://github.com/ummarvali/AgenticSoftware/pull/11) | tests passed, but review found a defect they missed (see §7) |

Four earlier command-line runs are recorded under [`examples/`](examples/) and re-tested by CI.

## 3. My approach

- **I treated it as a reliability problem, not a chatbot problem.** "Controlled autonomy" and
  "validation" mean the system must prove its own output works before a human is asked to
  approve it, and must never half-complete: every failure retries, degrades or stops cleanly.
- **Agents do the work; the model does the reasoning.** Each agent (analyst, planner,
  architect, code/test/doc generators, validator, repair, summary writer) has one job and
  records why it acted. The reasoning comes from Claude, with a deterministic fallback per stage.
- **Humans decide at two points:** before spending (approve the run) and after seeing the
  result (accept it). Unclear requirements are asked about, not guessed.
- **I ran it the way a team would:** requests as issues, the key in GitHub's secret store,
  named approvers, output as pull requests that go through normal code review.
- **I wrote down what it does not do** (§7). For a prototype, honest limits matter more than
  feature count.

## 4. How it works

```
 Issue ─► ⏸ approve spend ─► GitHub Actions job
                                                    │
 ┌──────────────────── Orchestrator (one run) ──────▼─────────────────────────────────────┐
 │  Analyst ─► Planner ─► task graph, run level by level (independent tasks in parallel)  │
 │             Architect ─► [CodebaseAnalyst: for changes] ─► Code ─► Tests ─► Docs       │
 │  reasoning: Claude (analyse · plan · design · code + tests), deterministic fallback    │
 │  model code must pass: safety scan → compile → its own tests (one repair attempt)      │
 │  Validator ─► Repair (if fixable) ─► re-validate ─► engineering summary                │
 │  all agents share one state object and log every decision                              │
 └────────────────────────────────────────┬───────────────────────────────────────────────┘
                                           ▼
            result on the issue ─► ⏸ accept ─► pull request
```

Diagrams and control flow in detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## 5. Key decisions

| Decision | Why |
| --- | --- |
| Agents coordinate through one shared state, not by calling each other | every run is auditable end to end, and each agent can be tested alone |
| The work is a dependency graph, run level by level | real sequencing, parallel independent work, retry / degrade / halt per task |
| The model's code is accepted only after it compiles and its own tests pass | the output is verified, not just produced; a failure gets one repair attempt |
| A deterministic engine backs every model stage | a model error never half-completes a run, and everything can be tested without a key |
| Ask first when there is no safe default; otherwise assume and record | a vague goal is clarified before money is spent; details are stated as assumptions |
| Python standard library only, SQLite for storage | runs anywhere in seconds; the production path (Postgres, Redis, Kafka) is written as trade-offs |
| Changes to existing code are proposed as a change set, never applied directly | the repository is only changed through an accepted pull request |

## 6. Implementation and setup

**Layout**

| Path | What it is |
| --- | --- |
| [`src/agentic_sdlc/`](src/agentic_sdlc/) | the agent system: `agents/`, `orchestrator/`, `llm/` (Claude client, fallback engine), `prompts/`, `tools/` (safety scan, sandboxed test runner) |
| [`.github/workflows/agent.yml`](.github/workflows/agent.yml) | the pipeline: request → approve → run → report → accept → pull request |
| [`demo/`](demo/) | the URL shortener (runnable, tested) |
| [`examples/`](examples/) | sample requirements and four recorded live runs |
| [`tests/`](tests/) | 79 tests for the system itself |

**Run the agent locally**

```bash
cd AgenticSoftware
python3 -m venv .venv && source .venv/bin/activate    # Windows: python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -e ".[anthropic]"
export ANTHROPIC_API_KEY="<your key>"                   # Windows: $env:ANTHROPIC_API_KEY = "<your key>"
python -m agentic_sdlc --provider claude --file examples/greenfield.txt
python -m agentic_sdlc --provider claude --interactive --file examples/greenfield.txt   # approve each step yourself
```

Without a key, leave out `--provider claude` — `python -m agentic_sdlc --file examples/greenfield.txt`
runs the same agents on the deterministic engine (this is what CI runs).
Output goes to `runs/<run-id>/`: the generated project and `ENGINEERING_SUMMARY.md`.
To run the pipeline in your own GitHub fork with your own key, see
[the full reference](docs/REFERENCE.md#b-run-it-in-your-own-fork--your-key-your-approvals).

## 7. Validation, assumptions and trade-offs

**How output is validated:** a safety scan (dangerous calls, non-standard-library imports,
hard-coded secrets), compilation, the generated unit and integration tests run in an
isolated interpreter with a timeout, the API contract and docs checked for presence, and for a
change to existing code, that code's own tests re-run with the change applied. Then a human
accepts or rejects. The system's own 79 tests, the demo's 20, and a scorecard of 6 offline
scenarios plus 4 recorded runs run in CI on every push.

**Assumptions:** an unclear goal is asked about; other open details get a stated default
(listed in each summary). The generated service targets clarity and the standard library.

**Trade-offs and known limits:**
- A single-process prototype with SQLite and an in-process cache; each design lists the path to
  a distributed store, a shared cache and asynchronous analytics. "Scalable" is designed, not load-tested.
- The model writes the code *and* its tests, so tests check what the model chose to test. In
  PR #11 a rejected duplicate alias left a database transaction open — its tests passed; my
  review found it. That is why every result is reviewed; an adversarial "critic" agent is the next step.
- Design decisions are recorded but not each verified against the code; the API contract is
  checked for presence, not conformance.
- The test sandbox is a separate isolated process, not a network-isolated container. The key is
  never in the tests' environment, but the real controls are the maintainer's approval and a
  spend-limited key.
- The demo uses predictable sequential short codes; production would use random codes.

## 8. More detail

- [`docs/REFERENCE.md`](docs/REFERENCE.md) — the full reference: every scenario, the complete
  risk and security analysis, testing approach, requirement-coverage matrix, and operating model.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — components, execution model, diagrams.
- [`examples/README.md`](examples/README.md) — sample inputs and outputs (greenfield, brownfield, ambiguous).

I used AI coding assistants as pair-programmers during implementation; the architecture,
the reliability approach and the review of every module are mine.

**License:** MIT.
