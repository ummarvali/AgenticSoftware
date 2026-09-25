# Example Scenarios

The primary mode is the **live model** — three recorded runs are described in
[§4](#4-live-model--examplesllm-run-recorded). The three requirement files below exercise
the three input classes (greenfield, brownfield, ambiguous); the commands shown run them
on the offline fallback so they work without a key (add `--provider claude` to run them live).
Each run shows **task decomposition**, **multi-step orchestration**, and **output validation**.

Run any example (from the project root):

```powershell
$env:PYTHONPATH = "src"
python -m agentic_sdlc --file examples/greenfield.txt
python -m agentic_sdlc --file examples/brownfield.txt --repo .
python -m agentic_sdlc --file examples/ambiguous.txt
```

---

## 1. Greenfield — `examples/greenfield.txt`

> "Build a scalable URL shortener service with APIs, persistence, and analytics."

- **Classification:** `greenfield`, domain `url_shortener`, confidence `0.92`
- **Task graph (5 levels):** `design → (code ∥ docs) → tests → validate → summary`
- **Output:** 15 artifacts — full `url_shortener/` package, `openapi.yaml`, 3 test files,
  `README.md`, `docs/ARCHITECTURE.md`, `ENGINEERING_SUMMARY.md`
- **Validation:** `5/5 checks passed` (code compiles, tests pass, contract present, docs present, static safety scan)

```
[orchestrator] --- level 0: design ---
[orchestrator] --- level 1: code, docs ---   <- parallelizable, not linear
[orchestrator] --- level 2: tests ---
[orchestrator] --- level 3: validate ---
[orchestrator] --- level 4: summary ---
Validation : 5/5 checks passed (PASS)
```

## 2. Brownfield — `examples/brownfield.txt`

> "Add rate limiting to the existing URL shortener API to prevent abuse."

- **Classification:** `brownfield` (triggered by "existing")
- **Task graph:** adds an `impact` task that `code` depends on —
  `(design ∥ impact) → code → tests → …`
- **Codebase impact:** identifies affected modules and the new rate-limiter concern
  (see the *Codebase Impact* section in `ENGINEERING_SUMMARY.md`). Pass `--repo <path>`
  (e.g. `--repo demo`) to rank that repository's files by relevance to the change.
- **The change itself:** a per-client token-bucket limiter (`url_shortener/ratelimit.py`)
  wired into the server, HTTP 429 on `POST /api/shorten`, the contract updated, and
  `tests/test_ratelimit.py` — 17 artifacts vs 15 for greenfield.
- **Validation:** `5/5 checks passed` (22 generated tests)

## 3. Ambiguous — `examples/ambiguous.txt`

> "Make the app faster."

- **Classification:** `ambiguous` (short + vague adjective + no concrete deliverable)
- **Behaviour:** the analyst surfaces blocking questions and records a **default
  assumption** for each, so autonomy proceeds transparently. In `--interactive` mode a
  human resolves them at the clarification gate.
- **Validation:** the generic scaffold first fails `4/5` (no API contract); the Repair
  agent synthesizes it and re-validation passes `5/5` — the validation feedback loop in
  action. What guards against a false "done" here is the **clarification gate**: the
  assumptions are shown to a human before any work starts (and in auto mode they are
  recorded in the summary), not the validator.

---

## 4. Live model — `examples/llm-run*/` (recorded)

Three runs of the same pipeline driven by a real model (`--provider claude`), checked in as
**snapshots of actual runs** so the model-driven path can be inspected without any API key:

| Folder | Requirement | What it demonstrates |
| --- | --- | --- |
| `llm-run/` | the mandatory URL shortener | model-authored, SQLite-backed service with its own tests; design↔implementation coverage table |
| `llm-run-inventory/` | inventory service with low-stock alerts | a different domain through the same agents, gates and validator; the coverage table shows 14/15 designed endpoints served (the design-only one, `/openapi.json`, is stated as a limitation) |
| `llm-run-go-card-validator/` | a **Go** microservice validating card transactions | non-Python target: the design records Go, the validated slice is Python (stated as a limitation), with 15 model-written tests passing in the sandbox |

In each folder:

- `result.json` → `metrics.llm`: per-stage calls (`analyze`, `decompose`, `design`,
  `codegen`), prompt/completion tokens, latency, estimated cost, retries, and whether any
  stage fell back to the deterministic engine and why.
- `artifacts/` → the code and tests the model authored, accepted only after the sandbox
  gate (static scan, compile, its own tests; a rejected bundle gets one repair pass with the
  sandbox output; if `metrics.llm` shows a `codegen` fallback, the verified template replaced
  it — the record is honest either way). `openapi.yaml` and `README.md` are synthesized from
  the model's design by the Repair agent when the bundle leaves them out; the README says how
  to start the service.
- `artifacts/ENGINEERING_SUMMARY.md` → plan, rationale (design decisions + the agents'
  decision log), **design ↔ implementation coverage**, validation, risks derived from the
  produced slice, the model's trade-offs (prototype vs production), assumptions, limitations.
- The model plans a **richer DAG** (roughly 15–30 tasks, 10+ levels) than the offline engine's 6.
  The first `design`/`code`/`tests`/`docs` task does the work; later ones log a `reuse`
  decision (`reused=N` in the monitoring line), so every task completes without duplicate
  artifacts.

Refresh a folder after your own run: `python scripts/snapshot_run.py --name <folder>`.

## Demonstrating error handling & recovery

```powershell
# 'code' task fails once, then the orchestrator retries and recovers:
python -m agentic_sdlc --inject-fault code:1 --file examples/greenfield.txt

# 'docs' is optional: repeated failure degrades gracefully instead of halting:
python -m agentic_sdlc --inject-fault docs:5 --file examples/greenfield.txt

# a required task that never recovers halts the run cleanly (partial result saved):
python -m agentic_sdlc --inject-fault code:9 --file examples/greenfield.txt
```

Every run writes a full record to `runs/<run-id>/result.json` and a human-readable
`ENGINEERING_SUMMARY.md` alongside the generated artifacts.
