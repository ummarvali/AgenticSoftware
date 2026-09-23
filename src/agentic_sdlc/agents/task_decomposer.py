"""Task decomposer — builds the executable, dependency-aware task DAG."""

from __future__ import annotations

from agentic_sdlc.models import TaskGraph
from agentic_sdlc.orchestrator.state import AgentContext


class TaskDecomposerAgent:
    name = "TaskDecomposer"
    category = "decomposition"

    def build(self, ctx: AgentContext) -> TaskGraph:
        bb = ctx.blackboard
        assert bb.analysis is not None, "analysis must run before decomposition"
        ctx.emit(self.name, "decomposing normalized problem into a task graph")
        graph = ctx.provider.decompose(bb.analysis)
        graph.validate_acyclic()  # guardrail: refuse to execute an invalid plan
        levels = graph.topological_levels()
        bb.log(
            "decomposition",
            f"planned {len(graph.tasks)} tasks across {len(levels)} dependency levels",
            tasks=[t.id for t in graph.tasks],
        )
        ctx.emit(
            self.name,
            f"{len(graph.tasks)} tasks, {len(levels)} levels "
            f"({', '.join('|'.join(t.id for t in lvl) for lvl in levels)})",
        )
        return graph
