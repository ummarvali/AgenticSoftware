"""Codebase analyst — brownfield impact reasoning.

For a change to existing software, this agent identifies which modules, APIs, and
data flows are affected. If a real repository path is supplied it scans it for
relevant files; otherwise it reasons from the architecture and the change verb so
the pipeline still produces a defensible impact assessment.
"""

from __future__ import annotations

import os
import re
from typing import Any

from agentic_sdlc.agents.base import Agent, AgentDecision
from agentic_sdlc.models import Task
from agentic_sdlc.orchestrator.state import AgentContext

_STOPWORDS = {"that", "this", "with", "from", "into", "have", "should", "would", "existing",
              "service", "system", "prevent", "make", "build", "the", "and", "for"}
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
        assert bb.analysis is not None and bb.architecture is not None
        impact: list[str] = []

        if decision.action == "scan-repo":
            impact += self._scan_repo(decision.params["repo"], bb.requirement.text)

        # Reason from the design regardless, so we always give a system-level view.
        low = bb.requirement.text.lower()
        verbs = [v for v in _CHANGE_VERBS if v in low] or ["change"]
        for comp in bb.architecture.components:
            module = comp.split("—")[0].strip()
            impact.append(f"{module}: assess for '{', '.join(verbs)}' impact")

        if "rate" in low:
            impact.append("New concern: request-rate accounting -> add a limiter "
                          "module and 429 responses; store per-key counters/windows.")

        bb.impact = impact
        bb.log("impact", f"identified {len(impact)} impacted areas")
        ctx.emit(self.name, f"identified {len(impact)} impacted areas")

    @staticmethod
    def _scan_repo(repo: str, requirement: str = "", limit: int = 10) -> list[str]:
        """Rank source files by how often the requirement's key terms appear in their
        path and content; return the top ``limit`` as candidate touch points. A
        heuristic (term overlap, not semantic search) — stated as such in the output."""

        terms = {w for w in re.findall(r"[a-z][a-z0-9_]{3,}", requirement.lower())
                 if w not in _STOPWORDS}
        if "rate" in requirement.lower():
            terms |= {"rate", "limit", "throttle", "429", "request", "route", "handler"}
        skip = {".git", ".venv", "venv", "node_modules", "__pycache__", "runs", "dist", "build"}
        scored: list[tuple[int, str]] = []
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
            for fname in files:
                if not fname.endswith((".py", ".ts", ".js", ".go", ".java")):
                    continue
                path = os.path.join(root, fname)
                rel = os.path.relpath(path, repo)
                try:
                    with open(path, encoding="utf-8", errors="ignore") as fh:
                        text = (rel + "\n" + fh.read(65536)).lower()
                except OSError:
                    continue
                score = sum(text.count(t) for t in terms)
                if score:
                    scored.append((score, rel))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [f"existing file (candidate touch point, term-overlap score {sc}): {rel}"
                for sc, rel in scored[:limit]]
