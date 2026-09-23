"""Architect agent — commits to a design before any code is generated.

It makes a real, recorded decision: whether to recommend a durable (SQLite) or
in-memory persistence default, inferred from the non-functional requirements. The
choice is written into the architecture's decision log, so the rationale is auditable.
"""

from __future__ import annotations

from typing import Any

from agentic_sdlc.agents.base import Agent, AgentDecision
from agentic_sdlc.models import Task
from agentic_sdlc.orchestrator.state import AgentContext

_DURABILITY_SIGNALS = ("scalable", "persistence", "durable", "durability", "production", "reliable")


class ArchitectAgent(Agent):
    name = "Architect"
    category = "design"

    def perceive(self, ctx: AgentContext, task: Task) -> dict[str, Any]:
        assert ctx.blackboard.analysis is not None
        return {"nfrs": ctx.blackboard.analysis.non_functional_requirements}

    def decide(self, ctx: AgentContext, obs: dict[str, Any]) -> AgentDecision:
        text = " ".join(obs["nfrs"]).lower()
        if any(sig in text for sig in _DURABILITY_SIGNALS):
            return AgentDecision(
                "design(durable)",
                "NFRs imply durability/scale → recommend the SQLite backend as default",
                params={"backend": "sqlite"},
            )
        return AgentDecision(
            "design(in-memory)",
            "no durability signal → in-memory default is sufficient for the prototype",
            params={"backend": "memory"},
        )

    def act(self, ctx: AgentContext, task: Task, decision: AgentDecision) -> None:
        bb = ctx.blackboard
        assert bb.analysis is not None
        arch = ctx.provider.design(bb.analysis)
        if not arch.components:
            raise ValueError("architecture produced no components")
        # Record the agent's persistence decision in the design's rationale log.
        arch.decisions.append(
            f"Persistence default: {decision.params['backend']} "
            f"({decision.rationale})."
        )
        bb.architecture = arch
        bb.log(
            "design",
            f"{len(arch.components)} components, {len(arch.api)} endpoints, "
            f"{len(arch.decisions)} decisions",
        )
        ctx.emit(self.name, f"designed {len(arch.components)} components, "
                            f"{len(arch.api)} API endpoints")
