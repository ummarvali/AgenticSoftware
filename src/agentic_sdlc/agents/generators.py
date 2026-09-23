"""Code, test, and documentation generator agents.

Grouped because they share a shape: perceive the design, decide to generate (or, for
code, whether this is a first pass vs. a later iteration), then act — recording the
artifacts on the blackboard and materializing them so downstream validation can
compile and execute them.
"""

from __future__ import annotations

from typing import Any

from agentic_sdlc.agents.base import Agent, AgentDecision
from agentic_sdlc.models import Task
from agentic_sdlc.orchestrator.state import AgentContext


class CodeGeneratorAgent(Agent):
    name = "CodeGenerator"
    category = "code"

    def perceive(self, ctx: AgentContext, task: Task) -> dict[str, Any]:
        return {"already_generated": bool(ctx.blackboard.code)}

    def decide(self, ctx: AgentContext, obs: dict[str, Any]) -> AgentDecision:
        if obs["already_generated"]:
            return AgentDecision(
                "regenerate",
                "code already exists on the blackboard → refresh from current design",
            )
        return AgentDecision("generate", "no code yet → generate from the design")

    def act(self, ctx: AgentContext, task: Task, decision: AgentDecision) -> None:
        bb = ctx.blackboard
        assert bb.analysis is not None and bb.architecture is not None
        artifacts = ctx.provider.generate_code(bb.analysis, bb.architecture)
        if not artifacts:
            raise ValueError("code generation produced no artifacts")
        bb.code.extend(artifacts)
        ctx.tools.artifacts.write_all(artifacts)  # persist so tests can import them
        bb.log("code", f"generated {len(artifacts)} code/contract files",
                files=[a.path for a in artifacts])
        ctx.emit(self.name, f"generated {len(artifacts)} files")


class TestGeneratorAgent(Agent):
    name = "TestGenerator"
    category = "tests"

    def decide(self, ctx: AgentContext, obs: dict[str, Any]) -> AgentDecision:
        assert ctx.blackboard.code, "tests require generated code"
        return AgentDecision("generate-tests",
                             "generate unit + integration tests for the code")

    def act(self, ctx: AgentContext, task: Task, decision: AgentDecision) -> None:
        bb = ctx.blackboard
        assert bb.analysis is not None and bb.architecture is not None
        artifacts = ctx.provider.generate_tests(bb.analysis, bb.architecture, bb.code)
        if not artifacts:
            raise ValueError("test generation produced no artifacts")
        bb.tests.extend(artifacts)
        ctx.tools.artifacts.write_all(artifacts)
        bb.log("tests", f"generated {len(artifacts)} test files",
                files=[a.path for a in artifacts])
        ctx.emit(self.name, f"generated {len(artifacts)} test files")


class DocGeneratorAgent(Agent):
    name = "DocGenerator"
    category = "docs"

    def decide(self, ctx: AgentContext, obs: dict[str, Any]) -> AgentDecision:
        return AgentDecision("generate-docs", "generate README and architecture docs")

    def act(self, ctx: AgentContext, task: Task, decision: AgentDecision) -> None:
        bb = ctx.blackboard
        assert bb.analysis is not None and bb.architecture is not None
        artifacts = ctx.provider.generate_docs(bb.analysis, bb.architecture)
        bb.docs.extend(artifacts)
        ctx.tools.artifacts.write_all(artifacts)
        bb.log("docs", f"generated {len(artifacts)} documentation files",
                files=[a.path for a in artifacts])
        ctx.emit(self.name, f"generated {len(artifacts)} documentation files")
