"""Codebase analyst — brownfield impact reasoning.

For a change to existing software, this agent identifies which modules, APIs, and
data flows are affected. If a real repository path is supplied it scans it for
relevant files; otherwise it reasons from the architecture and the change verb so
the pipeline still produces a defensible impact assessment.
"""

from __future__ import annotations

import os
from typing import Any

from agentic_sdlc.agents.base import Agent, AgentDecision
from agentic_sdlc.models import Task
from agentic_sdlc.orchestrator.state import AgentContext
from agentic_sdlc.tools import repo as repo_tool


def ensure_repo_loaded(bb) -> bool:
    """Brownfield with a real repository: snapshot it (read-only) onto the blackboard and
    switch the run to change mode. Safe to call from any agent, in any order."""

    path = bb.requirement.repo_path
    if not (path and os.path.isdir(path)):
        return False
    with bb._lock:                      # agents in one DAG level may race to load it
        if not bb.repo_files:
            bb.repo_files = repo_tool.snapshot(path)
            bb.change_mode = True
            bb.log("impact", f"repository snapshot: {len(bb.repo_files)} files (read-only); "
                             "this run proposes a change set against it")
    return True


_CHANGE_VERBS = ("add", "rate limit", "rate-limit", "refactor", "fix", "migrate",
                 "optimize", "cache", "auth", "secure")


class CodebaseAnalystAgent(Agent):
    name = "CodebaseAnalyst"
    category = "codebase_impact"

    def perceive(self, ctx: AgentContext, task: Task) -> dict[str, Any]:
        repo = ctx.blackboard.requirement.repo_path
        return {"repo": repo, "has_repo": bool(repo and os.path.isdir(repo))}

    def decide(self, ctx: AgentContext, obs: dict[str, Any]) -> AgentDecision:
        if obs["has_repo"]:
            return AgentDecision(
                "scan-repo",
                "a real repository path exists → scan it for candidate touch points",
                params={"repo": obs["repo"]},
            )
        return AgentDecision(
            "design-impact",
            "no repository provided → reason about impact from the architecture",
        )

    def act(self, ctx: AgentContext, task: Task, decision: AgentDecision) -> None:
        bb = ctx.blackboard
        assert bb.analysis is not None
        impact: list[str] = []

        if decision.action == "scan-repo":
            ensure_repo_loaded(bb)
            impact += [f"existing file (candidate touch point, term-overlap score {sc}): {p}"
                       for sc, p in repo_tool.rank(bb.repo_files, bb.requirement.text)
                       if p.endswith((".py", ".ts", ".js", ".go", ".java"))][:10]

        # Reason from the design regardless, so we always give a system-level view.
        low = bb.requirement.text.lower()
        verbs = [v for v in _CHANGE_VERBS if v in low] or ["change"]
        for comp in (bb.architecture.components if bb.architecture else []):
            module = comp.split("—")[0].strip()
            impact.append(f"{module}: assess for '{', '.join(verbs)}' impact")

        if "rate" in low:
            impact.append("New concern: request-rate accounting -> add a limiter "
                          "module and 429 responses; store per-key counters/windows.")

        bb.impact = impact
        bb.log("impact", f"identified {len(impact)} impacted areas")
        ctx.emit(self.name, f"identified {len(impact)} impacted areas")
