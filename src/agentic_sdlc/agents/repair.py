"""Repair agent — closes the validation feedback loop.

When the validator reports a *repairable* finding (a missing API contract or missing
documentation), the orchestrator routes control here. The agent perceives the failing
checks, decides which it can fix, acts to produce the missing artifact, and the
orchestrator then re-validates. This is genuine agent-driven recovery — a decision
made from observed state, not a blind retry of identical work.
"""

from __future__ import annotations

from typing import Any

from agentic_sdlc.agents.base import Agent, AgentDecision
from agentic_sdlc.agents.validator import REPAIRABLE_CHECKS
from agentic_sdlc.models import Artifact, Task
from agentic_sdlc.orchestrator.state import AgentContext


class RepairAgent(Agent):
    name = "Repair"
    category = "repair"

    def perceive(self, ctx: AgentContext, task: Task) -> dict[str, Any]:
        report = ctx.blackboard.validation
        failing = [c.name for c in report.checks if not c.passed] if report else []
        return {"repairable": [c for c in failing if c in REPAIRABLE_CHECKS]}

    def decide(self, ctx: AgentContext, obs: dict[str, Any]) -> AgentDecision:
        fixes = obs["repairable"]
        if not fixes:
            return AgentDecision("no-op", "no auto-repairable findings", proceed=False)
        return AgentDecision("repair", f"auto-fixing: {', '.join(fixes)}",
                             params={"fixes": fixes})

    def act(self, ctx: AgentContext, task: Task, decision: AgentDecision) -> None:
        for name in decision.params["fixes"]:
            handler = self._HANDLERS.get(name)
            if handler:
                handler(self, ctx)
        ctx.blackboard.log("repair", f"applied fixes: {decision.params['fixes']}")
        ctx.emit(self.name, f"applied fixes: {', '.join(decision.params['fixes'])}")

    # -- fixers ------------------------------------------------------------ #

    def _add_contract(self, ctx: AgentContext) -> None:
        bb = ctx.blackboard
        endpoints = bb.architecture.api if bb.architecture else []
        paths = "\n".join(
            f"  {e.path}:\n    {e.method.lower()}:\n"
            f"      summary: {e.summary}\n"
            f"      responses:\n        \"{e.status}\": {{ description: OK }}"
            for e in endpoints
        ) or "  {}"
        content = ("openapi: 3.0.3\n"
                   "info:\n  title: Generated API\n  version: 1.0.0\n"
                   "paths:\n" + paths + "\n")
        artifact = Artifact("openapi.yaml", content, "contract")
        bb.merge(bb.code, [artifact])
        ctx.tools.artifacts.write(artifact)

    def _add_readme(self, ctx: AgentContext) -> None:
        bb = ctx.blackboard
        overview = bb.architecture.overview if bb.architecture else ""
        content = f"# Generated Service\n\n{overview}\n\n## Tests\n\n`python -m unittest discover -s tests`\n"
        artifact = Artifact("README.md", content, "docs")
        bb.merge(bb.docs, [artifact])
        ctx.tools.artifacts.write(artifact)

    _HANDLERS = {
        "api contract present": _add_contract,
        "documentation present": _add_readme,
    }
