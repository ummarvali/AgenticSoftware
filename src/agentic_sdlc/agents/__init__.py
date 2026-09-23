"""Agents — each owns one SDLC capability and coordinates via the blackboard."""

from agentic_sdlc.agents.architect import ArchitectAgent
from agentic_sdlc.agents.base import Agent
from agentic_sdlc.agents.codebase_analyst import CodebaseAnalystAgent
from agentic_sdlc.agents.generators import (
    CodeGeneratorAgent,
    DocGeneratorAgent,
    TestGeneratorAgent,
)
from agentic_sdlc.agents.repair import RepairAgent
from agentic_sdlc.agents.requirement_analyst import RequirementAnalystAgent
from agentic_sdlc.agents.summary import SummaryAgent
from agentic_sdlc.agents.task_decomposer import TaskDecomposerAgent
from agentic_sdlc.agents.validator import ValidatorAgent

#: Registry mapping a task category to the agent that fulfils it. The orchestrator
#: dispatches each DAG task through this table, so adding a capability is just a new
#: agent plus a new entry — no orchestrator changes.
DAG_AGENTS: dict[str, Agent] = {
    ArchitectAgent.category: ArchitectAgent(),
    CodebaseAnalystAgent.category: CodebaseAnalystAgent(),
    CodeGeneratorAgent.category: CodeGeneratorAgent(),
    TestGeneratorAgent.category: TestGeneratorAgent(),
    DocGeneratorAgent.category: DocGeneratorAgent(),
    ValidatorAgent.category: ValidatorAgent(),
    SummaryAgent.category: SummaryAgent(),
    RepairAgent.category: RepairAgent(),
}

__all__ = [
    "Agent",
    "RequirementAnalystAgent",
    "TaskDecomposerAgent",
    "ArchitectAgent",
    "CodebaseAnalystAgent",
    "CodeGeneratorAgent",
    "TestGeneratorAgent",
    "DocGeneratorAgent",
    "ValidatorAgent",
    "SummaryAgent",
    "RepairAgent",
    "DAG_AGENTS",
]
