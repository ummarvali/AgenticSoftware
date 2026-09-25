"""One-command narrated demo of the Agentic SDLC system.

Run it from the repository root:

    python scripts/demo.py                 # offline (deterministic) or LLM if a key is set
    python scripts/demo.py --provider claude   # force the Claude backend (needs ANTHROPIC_API_KEY)

It walks a reviewer through the full workflow on four scenarios and highlights the two
capabilities that matter most for reliability: fault injection + recovery, and monitoring.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

# Make `src/` importable when running straight from a clone (no install needed).
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentic_sdlc.models import Requirement  # noqa: E402
from agentic_sdlc.orchestrator import Orchestrator, OrchestratorConfig  # noqa: E402

RUNS = str(ROOT / "runs")


def banner(title: str, subtitle: str) -> None:
    print("\n" + "=" * 78)
    print(f"  {title}")
    print(f"  {subtitle}")
    print("=" * 78)


def run(title: str, subtitle: str, requirement: Requirement, **cfg) -> None:
    banner(title, subtitle)
    config = OrchestratorConfig(output_root=RUNS, verbose=True, **cfg)
    result = Orchestrator(config).run(requirement)
    run_metrics = (result.metrics or {}).get("run", {})
    print("\n  RESULT")
    print(f"    classification : {result.analysis.kind.value} "
          f"(domain={result.analysis.domain}, confidence={result.analysis.confidence:.2f})")
    print(f"    artifacts      : {len(result.artifacts)} -> {result.output_dir}")
    print(f"    validation     : {result.validation.summary if result.validation else 'n/a'}")
    print(f"    monitoring     : duration={run_metrics.get('duration_s')}s "
          f"retries={run_metrics.get('retries')} repairs={run_metrics.get('repairs')} "
          f"degraded={run_metrics.get('degradations')} gates={run_metrics.get('gates')}")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Narrated Agentic SDLC demo.")
    parser.add_argument("--provider", default="auto",
                        choices=["auto", "deterministic", "claude", "openai"])
    args = parser.parse_args(argv)
    p = args.provider

    print("Agentic SDLC — end-to-end demo")
    print("Provider:", p, "(auto uses an LLM when a key is set, else deterministic)")

    # 1) Mandatory greenfield use case.
    run("1/4 GREENFIELD — mandatory use case",
        "Understand -> decompose (DAG) -> design -> generate -> validate -> summarize",
        Requirement("Build a scalable URL shortener service with APIs, persistence, and analytics."),
        provider=p)

    # 2) Brownfield — codebase reasoning + an injected impact task before code.
    run("2/4 BROWNFIELD — enhancement to existing code",
        "Adds a codebase-impact step; --repo scans this repo for touch points",
        Requirement("Add rate limiting to the existing URL shortener API to prevent abuse.",
                    repo_path=str(ROOT)),
        provider=p)

    # 3) Ambiguous — clarification + the validation feedback loop self-heals 4/5 -> 5/5.
    run("3/4 AMBIGUOUS — under-specified request",
        "Surfaces assumptions; validator finds a gap; Repair agent fixes it; re-validates",
        Requirement("Make the app faster."),
        provider=p)

    # 4) Fault injection + recovery — the SRE headline.
    run("4/4 FAULT INJECTION & RECOVERY",
        "Force the 'code' task to fail once; orchestrator retries and recovers",
        Requirement("Build a scalable URL shortener service with APIs and analytics."),
        provider=p, inject_fault={"code": 1})

    print("\n" + "=" * 78)
    print("  Demo complete. Inspect any run at runs/<run-id>/result.json")
    print("  Run the generated service:  cd runs/<run-id>/artifacts && "
          "python -m url_shortener.server")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
