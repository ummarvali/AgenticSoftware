"""Turn a pipeline trigger into a validated agent request (used by .github/workflows/agent.yml).

Three ways in:

* ``workflow_dispatch`` — a maintainer fills in the requirement, repo folder and provider;
* an issue opened from the *Agent request* form — anyone (for example a reviewer) writes a
  requirement and picks its target (new project, or a change to demo/). The run is *ask-first*: if the analysis finds a
  question with no safe default, the agent asks it on the issue instead of guessing;
* an ``/answer`` comment on that issue (by its author or a maintainer) — the original
  requirement plus the answers are built, without asking again.

Issue and comment text is untrusted: it is parsed here, never interpolated into a shell
script, length-capped, the target folder must be one of an allow-list, and the provider is
always the live model. Nothing runs until a maintainer approves the ``agent-run`` environment.

    python scripts/pipeline_request.py resolve           # writes the request to $GITHUB_OUTPUT
    python scripts/pipeline_request.py report <run>      # the issue comment for a finished run
    python scripts/pipeline_request.py questions <run>   # the issue comment asking the questions
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
MAX_ANSWERS = 2000
ISSUE_TARGETS = {"": "", "demo": "demo"}          # folders an issue may target

# The issue form (.github/ISSUE_TEMPLATE/agent-request.yml) asks for a free-text requirement
# and a target; keys must match the form's "Target" options exactly.
TARGETS: dict[str, str] = {
    "New project": "",
    "Change to demo/ (the existing URL shortener service)": "demo",
}

# Issues opened with the earlier form (a "Scenario" dropdown of presets) are still read, so an
# /answer on such an issue keeps working.
LEGACY_SCENARIOS: dict[str, tuple[str, str]] = {
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
    if "Target" in fields:
        target = fields["Target"]
        if target not in TARGETS:
            raise ValueError("unknown target; use the Agent request issue form")
        requirement = fields.get("Requirement", "").strip()
        if not requirement:
            raise ValueError("write a requirement in the Requirement box")
        return requirement, TARGETS[target], "claude"
    scenario = fields.get("Scenario", "")
    if scenario not in LEGACY_SCENARIOS:
        raise ValueError("unknown request format; use the Agent request issue form")
    requirement, repo_path = LEGACY_SCENARIOS[scenario]
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


def _answers(comment: str) -> str:
    """The text after the '/answer' command (tolerates leading blank lines, quotes and code fences)."""
    m = re.search(r"^[\s>`]*/answer\b(.*)", comment, re.I | re.S | re.M)
    if not m:
        raise ValueError("an answer comment must contain /answer at the start of a line")
    text = m.group(1).replace("```", "").strip()
    if not text:
        raise ValueError("write your answers after /answer (or '/answer use the defaults')")
    if len(text) > MAX_ANSWERS:
        raise ValueError(f"the answers are longer than {MAX_ANSWERS} characters")
    return text


def resolve() -> int:
    event = os.environ.get("EVENT_NAME")
    ask_first = event == "issues"
    try:
        if event in ("issues", "issue_comment"):
            requirement, repo_path, provider = _from_issue(os.environ.get("ISSUE_BODY", ""))
        else:
            requirement, repo_path, provider = _from_dispatch()
        if not requirement:
            raise ValueError("the requirement is empty")
        if len(requirement) > MAX_REQUIREMENT:
            raise ValueError(f"the requirement is longer than {MAX_REQUIREMENT} characters")
        if event == "issue_comment":
            answers = _answers(os.environ.get("COMMENT_BODY", ""))
            requirement += ("\n\nAnswers from the requester to the agent's clarifying "
                            "questions (take these as decided; where an answer says to use "
                            "the defaults, use the stated default assumptions):\n" + answers)
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
    print(f"ask first   : {ask_first}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        delim = f"EOF_{secrets.token_hex(8)}"       # multi-line safe, not guessable from input
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"requirement<<{delim}\n{requirement}\n{delim}\n")
            fh.write(f"repo_path={repo_path}\nprovider={provider}\n")
            fh.write(f"ask_first={'true' if ask_first else 'false'}\n")
    return 0


def questions(run_dir: str) -> int:
    """Markdown for the issue comment that asks the agent's blocking questions."""
    data = json.loads((Path(run_dir) / "clarification.json").read_text(encoding="utf-8"))
    lines = ["**The agent needs answers before it builds anything.** Its analysis found "
             "questions with no safe default:", ""]
    for i, q in enumerate(data.get("questions", []), 1):
        lines.append(f"{i}. **{q.get('question', '')}**")
        if q.get("why_it_matters"):
            lines.append(f"   Why it matters: {q['why_it_matters']}")
        lines.append(f"   If you prefer the default: *{q.get('default_assumption', '')}*")
    lines += ["", "Reply with a comment that starts with `/answer` (plain text, no code block "
              "needed), for example:", "",
              "> /answer", "> 1. ...", "> 2. ...", "",
              "or `/answer use the defaults` to go ahead with the defaults above. "
              "Your answer starts a new run with the requirement plus your answers "
              "(a maintainer approves it, as before)."]
    print("\n".join(lines))
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
    if len(sys.argv) == 3 and sys.argv[1] == "questions":
        raise SystemExit(questions(sys.argv[2]))
    print(__doc__)
    raise SystemExit(2)
