"""Command-line interface for the Agentic SDLC system.

Usage examples:

    # The mandatory use case, non-interactive:
    python -m agentic_sdlc "Build a scalable URL shortener service with APIs, persistence, and analytics."

    # Interactive, with human approval at each checkpoint:
    python -m agentic_sdlc --interactive "Add rate limiting to the existing URL shortener API."

    # Demonstrate error handling & recovery by injecting a transient fault:
    python -m agentic_sdlc --inject-fault code:1 "Build a URL shortener."
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentic_sdlc.models import Requirement
from agentic_sdlc.orchestrator import Orchestrator, OrchestratorConfig


def _parse_faults(values: list[str] | None) -> dict[str, int]:
    faults: dict[str, int] = {}
    for item in values or []:
        category, _, times = item.partition(":")
        faults[category] = int(times or "1")
    return faults


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agentic-sdlc",
        description="Transform a software requirement into a reviewable engineering outcome.",
    )
    p.add_argument("requirement", nargs="?", help="Requirement text, or omit and use --file.")
    p.add_argument("--file", help="Read the requirement from a file instead of the argument.")
    p.add_argument("--repo", help="Path to an existing repo (enables brownfield reasoning).")
    p.add_argument("--interactive", action="store_true",
                   help="Prompt for human approval at each checkpoint.")
    p.add_argument("--provider", default="auto",
                   choices=["auto", "deterministic", "anthropic", "claude", "llm", "openai"],
                   help="Reasoning backend. 'auto' uses the LLM when a key/endpoint is "
                        "configured (ANTHROPIC_API_KEY / OPENAI_API_KEY / "
                        "AZURE_OPENAI_ENDPOINT / OPENAI_BASE_URL), else deterministic.")
    p.add_argument("--output-root", default="runs", help="Where run outputs are written.")
    p.add_argument("--inject-fault", action="append", metavar="CATEGORY[:N]",
                   help="Force a category to fail N times to demonstrate recovery.")
    p.add_argument("--quiet", action="store_true", help="Suppress step-by-step logging.")
    p.add_argument("--json", action="store_true", help="Print the full result as JSON.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    text = args.requirement
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8").strip()
    if not text:
        print("error: provide a requirement argument or --file", file=sys.stderr)
        return 2

    config = OrchestratorConfig(
        provider=args.provider,
        interactive=args.interactive,
        output_root=args.output_root,
        verbose=not args.quiet,
        inject_fault=_parse_faults(args.inject_fault),
    )
    requirement = Requirement(text=text, repo_path=args.repo)
    result = Orchestrator(config).run(requirement)

    if args.json:
        import json

        print(json.dumps(result.to_dict(), indent=2, default=str))
        return 0

    _print_report(result)
    return 0 if (result.validation and result.validation.passed) else 1


def _print_report(result) -> None:
    print("\n" + "=" * 70)
    print("ENGINEERING OUTCOME")
    print("=" * 70)
    if result.analysis:
        print(f"Classification : {result.analysis.kind.value} "
              f"(domain={result.analysis.domain}, "
              f"confidence={result.analysis.confidence:.2f})")
    print(f"Artifacts      : {len(result.artifacts)} files -> {result.output_dir}")
    if result.validation:
        print(f"Validation     : {result.validation.summary} "
              f"({'PASS' if result.validation.passed else 'REVIEW NEEDED'})")
    if result.metrics and result.metrics.get("calls"):
        m = result.metrics
        print(f"LLM usage      : {len(m['calls'])} calls, {m['total_tokens']} tokens, "
              f"~${m['est_cost_usd']:.4f}, {m['fallbacks']} fallbacks")
    print(f"Run record     : {result.output_dir.replace('artifacts', 'result.json')}")
    summary_path = Path(result.output_dir) / "ENGINEERING_SUMMARY.md"
    if summary_path.exists():
        print(f"Summary        : {summary_path}")
    print("=" * 70)


if __name__ == "__main__":
    raise SystemExit(main())
