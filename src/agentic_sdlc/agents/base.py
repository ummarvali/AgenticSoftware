"""Agent base class — an explicit perceive → decide → act loop.

What makes these *agents* rather than functions: each one **perceives** the shared
state, **decides** what to do (recording a rationale for auditability), and only then
**acts**. The decision step lets an agent adapt to context — skip work whose
preconditions are unmet, pick a strategy (which backend, which scan mode), or choose
a repair over a fresh generation. Coordination is entirely through the blackboard;
agents never call each other.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from agentic_sdlc.models import Task
from agentic_sdlc.orchestrator.state import AgentContext


@dataclass
class AgentDecision:
    """The output of an agent's reasoning step — what it will do, and why."""

    action: str                       # short label of the chosen action
    rationale: str                    # why this action, given the observation
    params: dict[str, Any] = field(default_factory=dict)
    proceed: bool = True              # False => the agent chooses to do nothing


class Agent(abc.ABC):
    """A single, replaceable unit of engineering work with its own decision loop."""

    #: Human-readable agent name (used in logs and the summary).
    name: str = "agent"
    #: Task category this agent fulfils; the orchestrator dispatches on it.
    category: str = "generic"

    def perceive(self, ctx: AgentContext, task: Task) -> dict[str, Any]:
        """Read the state an agent needs to decide. Override to observe more."""

        return {}

    @abc.abstractmethod
    def decide(self, ctx: AgentContext, obs: dict[str, Any]) -> AgentDecision:
        """Choose an action from the observation, with a recorded rationale."""

    @abc.abstractmethod
    def act(self, ctx: AgentContext, task: Task, decision: AgentDecision) -> None:
        """Carry out the chosen action, mutating ``ctx.blackboard``."""

    def run(self, ctx: AgentContext, task: Task) -> None:
        """Template method: perceive → decide (log rationale) → act."""

        obs = self.perceive(ctx, task)
        decision = self.decide(ctx, obs)
        ctx.blackboard.log(
            "decision",
            f"{self.name}: {decision.action} - {decision.rationale}",
            agent=self.name, action=decision.action, proceed=decision.proceed,
        )
        ctx.emit(self.name, f"decided: {decision.action} - {decision.rationale}")
        if not decision.proceed:
            ctx.blackboard.log("skip", f"{self.name} chose to skip: {decision.rationale}")
            return
        self.act(ctx, task, decision)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"<{type(self).__name__} category={self.category!r}>"
