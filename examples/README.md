# Example Scenarios

The primary way to run these is the **GitHub Actions pipeline**: open an issue from the
[Agent request form](https://github.com/ummarvali/AgenticSoftware/issues/new?template=agent-request.yml)
and pick the matching test case (examples: [PR #1](https://github.com/ummarvali/AgenticSoftware/pull/1) greenfield, [issue #2](https://github.com/ummarvali/AgenticSoftware/issues/2) →
[PR #3](https://github.com/ummarvali/AgenticSoftware/pull/3) and [issue #4](https://github.com/ummarvali/AgenticSoftware/issues/4) → [PR #5](https://github.com/ummarvali/AgenticSoftware/pull/5) brownfield, [issue #6](https://github.com/ummarvali/AgenticSoftware/issues/6) →
[PR #7](https://github.com/ummarvali/AgenticSoftware/pull/7) ambiguous). Four earlier CLI runs on the live model are described in
[§4](#4-live-model--examplesllm-run-recorded). The three requirement files below exercise
the three input classes (greenfield, brownfield, ambiguous); the commands shown run them
on the offline fallback so they work without a key (add `--provider claude` to run them live).
Each run shows **task decomposition**, **multi-step orchestration**, and **output validation**.

Run any example (from the project root):

```powershell
$env:PYTHONPATH = "src"
python -m agentic_sdlc --file examples/greenfield.txt
python -m agentic_sdlc --file examples/brownfield.txt --repo demo
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
  `design → (impact ∥ docs) → code → tests → …`
- **Codebase impact:** snapshots `demo/` (read-only), ranks its files by relevance, and lists
  the candidate touch points (the *Codebase Impact* section in `ENGINEERING_SUMMARY.md`).
- **The change set** (change mode — not a regenerated project): `url_shortener/ratelimit.py`
  (new), `url_shortener/server.py` and `openapi.yaml` (modified), `tests/test_ratelimit.py`
  (new), and `CHANGES.diff`; the summary has a *Proposed change set* table with +/- lines.
- **Validation:** `6/6 checks passed` — on a copy of `demo/` with the change applied, the 20
  existing tests plus the 2 new ones pass (22); `change set present` is the sixth check.
  `demo/` is never modified.
- **Any other change** (bug fix, refactor, tests, docs): run with `--provider claude`; the
  model gets the relevant files and returns only the changed ones. Offline, the engine
  reports `change set present: FAIL` rather than inventing a change.

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

Four runs of the same pipeline driven by a real model (`--provider claude`), checked in as
**snapshots of actual runs** so the model-driven path can be inspected without any API key:

| Folder | Requirement | What it demonstrates |
| --- | --- | --- |
| `llm-run/` | the mandatory URL shortener | model-authored, SQLite-backed service with its own tests; design↔implementation coverage table |
| `llm-run-inventory/` | inventory service with low-stock alerts | a different domain through the same agents, gates and validator; the coverage table shows which designed endpoints the slice serves |
| `llm-run-brownfield/` | "Add rate limiting to the existing URL shortener API" with `--repo demo` | **change mode**: the model returns only the changed files; `CHANGES.diff` is the reviewable patch; demo's existing tests plus the new ones pass on a copy of `demo/` with the change applied; `demo/` is untouched |
| `llm-run-go-card-validator/` | a **Go** microservice validating card transactions | non-Python target: the design records Go, the validated slice is Python (stated as a limitation), with its model-written tests passing in the sandbox |

In each folder:

- `result.json` → `metrics.llm`: per-stage calls (`analyze`, `decompose`, `design`,
  `codegen`), model, prompt/completion tokens, latency, estimated cost (with the price source), retries, and whether any
  stage fell back to the deterministic engine and why.
- `artifacts/` → the code and tests the model authored, accepted only after the sandbox
  gate (static scan, compile, its own tests; a rejected bundle gets one repair pass with the
  sandbox output; if `metrics.llm` shows a `codegen` fallback, the verified template replaced
  it — the record is honest either way). The model writes `openapi.yaml` and `README.md` as
  part of the bundle; if it leaves either out, the Repair agent synthesizes it from the design.
- `artifacts/ENGINEERING_SUMMARY.md` → plan, rationale (design decisions + the agents'
  decision log), **design ↔ implementation coverage**, validation, risks derived from the
  produced slice, the model's trade-offs (prototype vs production), assumptions, limitations.
- The model plans a **richer DAG** (roughly 15–30 tasks, 10+ levels) than the offline engine's 6–7.
  The first `design`/`code`/`tests`/`docs` task does the work; later ones log a `reuse`
  decision (`reused=N` in the monitoring line), so every task completes without duplicate
  artifacts.

Refresh a folder after your own run: `python scripts/snapshot_run.py --name <folder>`.

## Demonstrating error handling & recovery

```powershell
# 'code' task fails once, then the orchestrator retries and recovers:
python -m agentic_sdlc --inject-fault code:1 --file examples/greenfield.txt

# 'docs' is optional: repeated failure degrades gracefully instead of halting:
python -m agentic_sdlc --inject-fault docs:9 --file examples/greenfield.txt

# a required task that never recovers halts the run cleanly (partial result saved):
python -m agentic_sdlc --inject-fault code:9 --file examples/greenfield.txt
```

Every run writes a full record to `runs/<run-id>/result.json` and a human-readable
`ENGINEERING_SUMMARY.md` alongside the generated artifacts.
