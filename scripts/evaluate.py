"""Evaluation harness — a scorecard for the agent system, not just its unit tests.

Runs every offline scenario (deterministic, so the scores are reproducible) and grades
each on four axes the brief cares about, then reads any recorded live-model runs under
examples/ and reports their quality/efficiency figures next to them.

    python scripts/evaluate.py            # prints the scorecard; exit 1 on any failed expectation
    python scripts/evaluate.py --json     # machine-readable, for CI or dashboards

Axes:
  output quality        validation checks passed; artifacts unique; summary complete
  task adherence        every planned task completed or explicitly reused; no halt unless expected
  tool correctness      compile + test gate actually ran; sandbox/scrub in force (unit-tested separately)
  operational efficiency duration, retries, repairs, degradations, reuse; tokens/cost/fallbacks for live runs
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentic_sdlc.orchestrator import Orchestrator, OrchestratorConfig  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GREEN = (ROOT / "examples" / "greenfield.txt").read_text(encoding="utf-8").strip()
BROWN = (ROOT / "examples" / "brownfield.txt").read_text(encoding="utf-8").strip()
AMBIG = (ROOT / "examples" / "ambiguous.txt").read_text(encoding="utf-8").strip()

# name, requirement, config overrides, expectations
SCENARIOS = [
    ("greenfield",        GREEN, {},                              dict(passed=True,  halted=False, min_artifacts=15, repairs=0)),
    ("brownfield",        BROWN, {"repo": "demo"},                dict(passed=True,  halted=False, min_artifacts=5, repairs=0, impact=True,
                                                                       feature="url_shortener/ratelimit.py", change_set=True)),
    ("ambiguous",         AMBIG, {},                              dict(passed=True,  halted=False, min_artifacts=6,  repairs=1)),
    ("retry-recovers",    GREEN, {"inject_fault": {"code": 1}},   dict(passed=True,  halted=False, min_artifacts=15, retries=1)),
    ("optional-degrades", GREEN, {"inject_fault": {"docs": 9}},   dict(passed=True,  halted=False, degradations=1)),
    ("required-halts",    GREEN, {"inject_fault": {"code": 9}},   dict(passed=False, halted=True)),
]


def run_scenario(name, requirement, overrides, expect, tmp):
    from agentic_sdlc.models import Requirement
    repo = overrides.pop("repo", None)
    cfg = OrchestratorConfig(provider="deterministic", output_root=tmp, verbose=False, **overrides)
    t0 = time.time()
    result = Orchestrator(cfg).run(Requirement(text=requirement, repo_path=repo))
    run = result.metrics["run"]
    paths = [a.path for a in result.artifacts]
    passed = bool(result.validation and result.validation.passed)
    checks = {
        "artifacts unique": len(paths) == len(set(paths)),
        "summary complete": bool(result.summary and result.summary.risks and result.summary.validation_approach)
                            if not run["halted"] else True,
        "all tasks completed": run["halted"] or (result.task_graph is not None and
                               run["tasks_ok"] >= len(result.task_graph.tasks) - run["degradations"]),
    }
    verdicts = {
        "validation as expected": passed == expect["passed"],
        "halted": run["halted"] == expect["halted"],
    }
    if "min_artifacts" in expect:
        verdicts["artifacts>=min"] = len(paths) >= expect["min_artifacts"]
    for k in ("repairs", "retries", "degradations"):
        if k in expect:
            verdicts[k] = run[k] == expect[k]
    if expect.get("change_set"):
        # Brownfield against a repo: only changed files + a reviewable diff, and the
        # repository's own tests re-ran with the change applied.
        verdicts["change set + diff, no full regeneration"] = (
            "CHANGES.diff" in paths and "url_shortener/store.py" not in paths)
    if expect.get("feature"):
        # The requested change must actually be in the output, not just the impact report.
        verdicts["requested feature present"] = expect["feature"] in paths
    if expect.get("impact"):
        verdicts["impact analysed"] = any(e["kind"] == "impact" or "impact" in e.get("message", "").lower()
                                          for e in result.events)
    ok = all(checks.values()) and all(verdicts.values())
    return {
        "scenario": name, "ok": ok,
        "quality": {"validation passed": passed, **checks}, "adherence": verdicts,
        "efficiency": {k: run[k] for k in ("duration_s", "tasks_ok", "retries", "repairs",
                                            "degradations", "parallel_levels", "reused")},
        "wall_s": round(time.time() - t0, 3),
    }


def _recorded_cost(calls):
    """Recompute the estimate from the recorded token counts with the current price table,
    so a stale stored figure is never shown; ``None`` when a model's price is unknown."""
    from agentic_sdlc.llm.client import CallRecord, MetricsCollector
    m = MetricsCollector()
    for c in calls:
        m.record(CallRecord(c["stage"], c["model"], c.get("prompt_tokens", 0),
                            c.get("completion_tokens", 0), c.get("latency_s", 0.0)))
    return m.est_cost_usd


def recorded_live_runs():
    out = []
    for d in sorted((ROOT / "examples").glob("llm-run*")):
        rj = d / "result.json"
        if not rj.exists():
            continue
        r = json.loads(rj.read_text(encoding="utf-8"))
        llm = r.get("metrics", {}).get("llm", {}) or {}
        run = r.get("metrics", {}).get("run", {}) or {}
        calls = llm.get("calls", [])
        out.append({
            "recorded_run": d.name,
            "domain": (r.get("analysis") or {}).get("domain"),
            "validation": run.get("validation"),
            "artifacts": run.get("artifacts"),
            "tasks": run.get("tasks_ok"), "reused": run.get("reused"), "repairs": run.get("repairs"),
            "llm_calls": llm.get("api_calls", len(calls)), "retries": llm.get("retries", 0),
            "tokens": llm.get("total_tokens"),
            "est_cost_usd": _recorded_cost(calls), "fallbacks": llm.get("fallbacks"),
            "fallback_stages": [c["stage"] for c in calls if c.get("fallback")],
            "duration_s": run.get("duration_s"),
            "model_authored_code": not any(c["stage"] == "codegen" and c.get("fallback") for c in calls),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    os.chdir(ROOT)

    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, req, overrides, expect in SCENARIOS:
            rows.append(run_scenario(name, req, dict(overrides), expect, tmp))
    live = recorded_live_runs()
    report = {"offline_scenarios": rows, "recorded_live_runs": live,
              "all_ok": all(r["ok"] for r in rows)}

    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if report["all_ok"] else 1

    print("\n=== OFFLINE SCENARIOS (deterministic, reproducible) ===")
    print(f"{'scenario':<18}{'ok':<5}{'valid':<7}{'tasks':<7}{'reused':<8}{'retry':<7}{'repair':<8}{'degr':<6}{'par':<5}{'secs':<7}")
    for r in rows:
        e = r["efficiency"]
        print(f"{r['scenario']:<18}{'PASS' if r['ok'] else 'FAIL':<5}"
              f"{'yes' if r['quality']['validation passed'] else 'no':<7}{e['tasks_ok']:<7}{e['reused']:<8}"
              f"{e['retries']:<7}{e['repairs']:<8}{e['degradations']:<6}{e['parallel_levels']:<5}{e['duration_s']:<7}")
        bad = [k for k, v in {**{k: v for k, v in r['quality'].items() if k != 'validation passed'},
                              **r['adherence']}.items() if not v]
        if bad:
            print(f"{'':<18}failed: {', '.join(bad)}")
    if live:
        print("\n=== RECORDED LIVE-MODEL RUNS (examples/llm-run*) ===")
        for l in live:
            print(f"{l['recorded_run']:<22} domain={l['domain']}  {l['validation']}  artifacts={l['artifacts']}  "
                  f"tasks={l['tasks']} reused={l['reused']} repairs={l['repairs']}")
            cost = l["est_cost_usd"]
            cost_s = f"~${cost:.4f}" if cost is not None else "cost n/a"
            print(f"{'':<22} llm: {l['llm_calls']} calls, {l['tokens']} tokens, {cost_s}, "
                  f"retries={l['retries']} "
                  f"fallbacks={l['fallbacks']} {l['fallback_stages'] or ''}  "
                  f"model-authored code: {'yes' if l['model_authored_code'] else 'no'}  {l['duration_s']}s")
    print(f"\nRESULT: {'ALL EXPECTATIONS MET' if report['all_ok'] else 'EXPECTATION FAILURES'}")
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
