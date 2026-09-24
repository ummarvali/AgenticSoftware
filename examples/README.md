# Example Scenarios

Three requirements exercise the three input classes the system must handle. Each run
shows **task decomposition**, **multi-step orchestration**, and **output validation**.
A fourth, recorded scenario shows the **same pipeline driven by a live model**.

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
  to also scan a real repository for candidate touch points.
- **Validation:** `5/5 checks passed`

## 3. Ambiguous — `examples/ambiguous.txt`

> "Make the app faster."

- **Classification:** `ambiguous` (short + vague adjective + no concrete deliverable)
- **Behaviour:** the analyst surfaces blocking questions and records a **default
  assumption** for each, so autonomy proceeds transparently. In `--interactive` mode a
  human resolves them at the clarification gate.
- **Validation:** `4/5 checks — REVIEW NEEDED`. This is the *correct* outcome: the system
  refuses to claim success on an under-specified request and flags it for a human.

---

## 4. Live model — `examples/llm-run/` (recorded)

The same greenfield requirement, run with `--provider openai` against a real model
(see the README for the free Gemini setup). This folder is a **snapshot of an actual
run**, checked in so the model-driven path can be inspected without any API key:

- `result.json` → `metrics.llm`: per-stage calls (`analyze`, `decompose`, `design`,
  `codegen`), prompt/completion tokens, latency, estimated cost, and whether any stage
  fell back to the deterministic engine and why.
- `artifacts/` → the project the model authored, which passed the sandbox compile+test
  gate before being accepted (or, if `metrics.llm` shows a `codegen` fallback, the
  verified template that replaced it — the record is honest either way).
- The model typically plans a **richer DAG** (20+ tasks, 10+ levels) than the offline
  engine's 6. Watch the agents handle it: the first `design`/`code`/`tests`/`docs` task
  does the work; later ones log a `reuse` decision (`reused=N` in the monitoring line),
  so the artifact set stays at 15 unique files and every task still completes.
- Compare with a deterministic run of the same input: same gates, same validator —
  different brain, different plan.

Refresh it with `python scripts/snapshot_run.py --name llm-run` after your own run.

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
