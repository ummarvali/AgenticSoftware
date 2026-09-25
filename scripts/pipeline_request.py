"""Turn a pipeline trigger into a validated agent request (used by .github/workflows/agent.yml).

Two ways in:

* ``workflow_dispatch`` — a maintainer fills in the requirement, repo folder and provider;
* an issue opened from the *Agent request* form — anyone (for example a reviewer) picks a
  preset scenario or writes a requirement. The issue body is untrusted text: it is parsed
  here, never interpolated into a shell script, the requirement is length-capped, the
  target folder must be one of an allow-list, and the provider is always the live model.
  Nothing runs until a maintainer approves the ``agent-run`` environment.

    python scripts/pipeline_request.py resolve        # writes requirement/repo_path/provider to $GITHUB_OUTPUT
    python scripts/pipeline_request.py report <run>   # prints the issue comment for a finished run
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_REQUIREMENT = 2000
ISSUE_TARGETS = {"": "", "demo": "demo"}          # folders an issue may target

# Preset scenarios offered by the issue form (.github/ISSUE_TEMPLATE/agent-request.yml).
# Keys must match the form's dropdown options exactly.
SCENARIOS: dict[str, tuple[str, str]] = {
    "Greenfield: URL shortener (the mandatory use case)": (
        (ROOT / "examples" / "greenfield.txt").read_text(encoding="utf-8").strip(), ""),
    "Greenfield: another domain (inventory with low-stock alerts)": (
        "Build an inventory service with REST APIs to add, adjust and query stock levels "
        "per warehouse, with low-stock alerts.", ""),
    "Brownfield enhancement: add rate limiting to demo/": (
        (ROOT / "examples" / "brownfield.txt").read_text(encoding="utf-8").strip(), "demo"),
    "Vague requirement: \"Make the app faster.\" against demo/ (the agent must detect the ambiguity)": (
        (ROOT / "examples" / "ambiguous.txt").read_text(encoding="utf-8").strip(), "demo"),
    "Custom: new project (write the requirement below)": ("", ""),
    "Custom: change to demo/ — bug fix, refactor, tests or docs (write it below)": ("", "demo"),
}


def _form_fields(body: str) -> dict[str, str]:
    """Parse a GitHub issue-form body: '### Label' headings followed by the answer."""
    fields: dict[str, str] = {}
    for m in re.finditer(r"^### (.+?)\r?\n(.*?)(?=^### |\Z)", body, re.S | re.M):
        value = m.group(2).strip()
        fields[m.group(1).strip()] = "" if value == "_No response_" else value
    return fields


def _from_issue(body: str) -> tuple[str, str, str]:
    fields = _form_fields(body)
    scenario = fields.get("Scenario", "")
    if scenario not in SCENARIOS:
        raise ValueError("unknown scenario; use the Agent request issue form")
    requirement, repo_path = SCENARIOS[scenario]
    written = fields.get("Requirement", "").strip()
    if not requirement:
        requirement = written
    elif written:
        requirement = written                 # an explicit requirement overrides the preset text
    if not requirement:
        raise ValueError("this scenario needs a requirement: write it in the Requirement box")
    if repo_path not in ISSUE_TARGETS.values():
        raise ValueError("target folder not allowed")
    return requirement, repo_path, "claude"


def _from_dispatch() -> tuple[str, str, str]:
    requirement = os.environ.get("IN_REQUIREMENT", "").strip()
    repo_path = os.environ.get("IN_REPO_PATH", "").strip()
    provider = os.environ.get("IN_PROVIDER", "claude").strip()
    if provider not in {"claude", "openai", "deterministic"}:
        raise ValueError(f"unknown provider {provider!r}")
    if repo_path:
        target = (ROOT / repo_path).resolve()
        if not target.is_dir() or ROOT not in target.parents:
            raise ValueError("repo_path must be a folder inside this repository")
    return requirement, repo_path, provider


def resolve() -> int:
    try:
        if os.environ.get("EVENT_NAME") == "issues":
            requirement, repo_path, provider = _from_issue(os.environ.get("ISSUE_BODY", ""))
        else:
            requirement, repo_path, provider = _from_dispatch()
        if not requirement:
            raise ValueError("the requirement is empty")
        if len(requirement) > MAX_REQUIREMENT:
            raise ValueError(f"the requirement is longer than {MAX_REQUIREMENT} characters")
    except ValueError as exc:
        print(f"::error::invalid agent request: {exc}")
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as fh:
                fh.write(f"error={exc}\n")
        return 1
    print(f"requirement : {requirement}")
    print(f"target      : {repo_path or '(new project)'}")
    print(f"provider    : {provider}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        delim = f"EOF_{secrets.token_hex(8)}"       # multi-line safe, not guessable from input
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"requirement<<{delim}\n{requirement}\n{delim}\n")
            fh.write(f"repo_path={repo_path}\nprovider={provider}\n")
    return 0


def report(run_dir: str) -> int:
    """Markdown for the issue comment: verdict, checks, model usage, then the full summary."""
    run = Path(run_dir)
    result = json.loads((run / "result.json").read_text(encoding="utf-8"))
    checks = result.get("validation", {}).get("checks", [])
    passed = sum(1 for c in checks if c.get("passed"))
    llm = result.get("metrics", {}).get("llm") or {}
    cost = llm.get("est_cost_usd")
    lines = [
        f"**Agent run `{result.get('run_id', run.name)}` finished — "
        f"{passed}/{len(checks)} validation checks passed.**",
        "",
        "| Check | Result | Detail |",
        "| --- | --- | --- |",
    ]
    for c in checks:
        detail = str(c.get("detail", "")).replace("|", "\\|").replace("\n", " ")[:160]
        lines.append(f"| {c.get('name')} | {'✅' if c.get('passed') else '❌'} | {detail} |")
    if llm:
        lines += ["", f"Model usage: {llm.get('api_calls', 0)} API calls, "
                      f"{llm.get('total_tokens', 0)} tokens, "
                      f"{'~$%.2f' % cost if cost is not None else 'cost n/a'}, "
                      f"retries {llm.get('retries', 0)}, fallbacks {llm.get('fallbacks', 0)}."]
    summary = run / "artifacts" / "ENGINEERING_SUMMARY.md"
    if summary.exists():
        text = summary.read_text(encoding="utf-8")[:50000]
        lines += ["", "<details><summary>Engineering summary</summary>", "", text, "", "</details>"]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "resolve":
        raise SystemExit(resolve())
    if len(sys.argv) == 3 and sys.argv[1] == "report":
        raise SystemExit(report(sys.argv[2]))
    print(__doc__)
    raise SystemExit(2)
