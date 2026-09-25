"""Code, test, and documentation generator agents.

Grouped because they share a shape: perceive the design, decide to generate (or, for
code, whether this is a first pass vs. a later iteration), then act — recording the
artifacts on the blackboard and materializing them so downstream validation can
compile and execute them.
"""

from __future__ import annotations

from typing import Any

from agentic_sdlc.agents.base import Agent, AgentDecision
from agentic_sdlc.agents.codebase_analyst import ensure_repo_loaded
from agentic_sdlc.models import Artifact, Task
from agentic_sdlc.orchestrator.state import AgentContext
from agentic_sdlc.tools import repo as repo_tool


def _section(path: str) -> str:
    if path.startswith("tests/") or "/tests/" in path:
        return "tests"
    if path.endswith((".md", ".rst", ".txt")):
        return "docs"
    return "code"


class CodeGeneratorAgent(Agent):
    name = "CodeGenerator"
    category = "code"

    def perceive(self, ctx: AgentContext, task: Task) -> dict[str, Any]:
        bb = ctx.blackboard
        change = ensure_repo_loaded(bb)
        done = bool(bb.code) or (change and any(e["kind"] == "change" for e in bb.events))
        return {"already_generated": done, "change_mode": change, "task": task.id}

    def decide(self, ctx: AgentContext, obs: dict[str, Any]) -> AgentDecision:
        if obs["change_mode"] and not obs["already_generated"]:
            return AgentDecision(
                "propose-change",
                "an existing repository was given → propose a change set against it "
                "(only new/changed files, validated against the repo's own tests)")
        if obs["already_generated"]:
            # The project is authored as one consistent bundle (code + contract). A
            # fine-grained plan may list several code tasks; they are satisfied by that
            # bundle, so re-running would only duplicate work — reuse instead.
            return AgentDecision(
                "reuse",
                f"code already generated from the current design; "
                f"'{obs['task']}' is covered by it",
                proceed=False,
            )
        return AgentDecision("generate", "no code yet → generate from the design")

    def act(self, ctx: AgentContext, task: Task, decision: AgentDecision) -> None:
        bb = ctx.blackboard
        assert bb.analysis is not None and bb.architecture is not None
        if decision.action == "propose-change":
            self._propose_change(ctx)
            return
        artifacts = ctx.provider.generate_code(bb.analysis, bb.architecture)
        if not artifacts:
            raise ValueError("code generation produced no artifacts")
        artifacts = bb.merge(bb.code, artifacts)
        ctx.tools.artifacts.write_all(artifacts)  # persist so tests can import them
        bb.log("code", f"generated {len(artifacts)} code/contract files",
                files=[a.path for a in artifacts])
        ctx.emit(self.name, f"generated {len(artifacts)} files")

    def _propose_change(self, ctx: AgentContext) -> None:
        bb = ctx.blackboard
        proposed, summary = ctx.provider.generate_change(
            bb.analysis, bb.architecture, bb.requirement.text, bb.repo_files,
            bb.requirement.repo_path or "")
        changes = repo_tool.only_changes(bb.repo_files, proposed)
        bb.change_summary = summary
        for sect in ("code", "tests", "docs"):
            bb.merge(getattr(bb, sect), [Artifact(a.path, a.content, a.kind if sect == "code" else
                                                  ("test" if sect == "tests" else "docs"))
                                         for a in changes if _section(a.path) == sect])
        written = list(changes)
        if changes:
            diff = Artifact("CHANGES.diff", repo_tool.unified_diff(bb.repo_files, changes), "change")
            bb.merge(bb.docs, [diff])
            written.append(diff)
        ctx.tools.artifacts.write_all(written)
        stats = repo_tool.diff_stats(bb.repo_files, changes)
        bb.log("change", f"proposed {len(changes)} changed file(s)",
               files=[f"{p} ({kind}, +{a}/-{r})" for p, kind, a, r in stats])
        ctx.emit(self.name, f"proposed change set: {len(changes)} file(s)"
                            + (f" — {', '.join(p for p, *_ in stats)}" if stats else
                               " — none authored (see validation)"))


class TestGeneratorAgent(Agent):
    name = "TestGenerator"
    category = "tests"

    def perceive(self, ctx: AgentContext, task: Task) -> dict[str, Any]:
        return {"already_generated": bool(ctx.blackboard.tests),
                "change_mode": ensure_repo_loaded(ctx.blackboard), "task": task.id}

    def decide(self, ctx: AgentContext, obs: dict[str, Any]) -> AgentDecision:
        if obs["change_mode"]:
            return AgentDecision(
                "reuse", f"change mode: tests are part of the proposed change set and the "
                         f"repository's own suite; '{obs['task']}' is covered", proceed=False)
        assert ctx.blackboard.code, "tests require generated code"
        if obs["already_generated"]:
            return AgentDecision(
                "reuse", f"test suite already generated; '{obs['task']}' is covered by it",
                proceed=False,
            )
        return AgentDecision("generate-tests",
                             "generate unit + integration tests for the code")

    def act(self, ctx: AgentContext, task: Task, decision: AgentDecision) -> None:
        bb = ctx.blackboard
        assert bb.analysis is not None and bb.architecture is not None
        artifacts = ctx.provider.generate_tests(bb.analysis, bb.architecture, bb.code)
        if not artifacts:
            raise ValueError("test generation produced no artifacts")
        artifacts = bb.merge(bb.tests, artifacts)
        ctx.tools.artifacts.write_all(artifacts)
        bb.log("tests", f"generated {len(artifacts)} test files",
                files=[a.path for a in artifacts])
        ctx.emit(self.name, f"generated {len(artifacts)} test files")


class DocGeneratorAgent(Agent):
    name = "DocGenerator"
    category = "docs"

    def perceive(self, ctx: AgentContext, task: Task) -> dict[str, Any]:
        return {"already_generated": bool(ctx.blackboard.docs),
                "attempted": ctx.blackboard.docs_attempted,
                "change_mode": ensure_repo_loaded(ctx.blackboard), "task": task.id}

    def decide(self, ctx: AgentContext, obs: dict[str, Any]) -> AgentDecision:
        if obs["change_mode"]:
            return AgentDecision(
                "reuse", f"change mode: documentation changes are part of the proposed "
                         f"change set; '{obs['task']}' is covered", proceed=False)
        if obs["already_generated"]:
            return AgentDecision(
                "reuse", f"documentation already generated; '{obs['task']}' is covered by it",
                proceed=False,
            )
        if obs["attempted"]:
            return AgentDecision(
                "defer", "docs stage already ran and produced nothing; the Repair agent "
                         "synthesizes docs from the design after validation",
                proceed=False,
            )
        return AgentDecision("generate-docs", "generate README and architecture docs")

    def act(self, ctx: AgentContext, task: Task, decision: AgentDecision) -> None:
        bb = ctx.blackboard
        assert bb.analysis is not None and bb.architecture is not None
        artifacts = ctx.provider.generate_docs(bb.analysis, bb.architecture)
        bb.docs_attempted = True
        artifacts = bb.merge(bb.docs, artifacts)
        ctx.tools.artifacts.write_all(artifacts)
        bb.log("docs", f"generated {len(artifacts)} documentation files",
                files=[a.path for a in artifacts])
        ctx.emit(self.name, f"generated {len(artifacts)} documentation files")
