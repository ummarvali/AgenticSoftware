"""Summary agent — writes the final structured engineering summary.

Consolidates everything on the blackboard into the deliverable a reviewer reads:
the plan and rationale, the artifacts produced, validation outcome, risks and
trade-offs, and the assumptions and limitations the run operated under.
"""

from __future__ import annotations

from typing import Any

from agentic_sdlc.agents.base import Agent, AgentDecision
from agentic_sdlc.models import Artifact, EngineeringSummary, Task
from agentic_sdlc.orchestrator.state import AgentContext


class SummaryAgent(Agent):
    name = "SummaryWriter"
    category = "summary"

    def decide(self, ctx: AgentContext, obs: dict[str, Any]) -> AgentDecision:
        assert ctx.blackboard.analysis is not None
        return AgentDecision("summarize", "consolidate the run into the final summary")

    def act(self, ctx: AgentContext, task: Task, decision: AgentDecision) -> None:
        bb = ctx.blackboard
        assert bb.analysis is not None

        # Idempotent across repair re-runs: drop any prior summary before rewriting.
        bb.docs[:] = [d for d in bb.docs if d.path != "ENGINEERING_SUMMARY.md"]

        summary = EngineeringSummary(
            requirement=bb.requirement.text,
            kind=bb.analysis.kind.value,
            implementation_plan=[
                "Analyze & normalize the requirement",
                "Design the architecture and API contract",
                *(["Assess brownfield codebase impact"] if bb.impact else []),
                "Generate implementation",
                "Generate unit + integration tests",
                "Generate documentation",
                "Validate (compile, test, contract, docs)",
                "Summarize for human review",
            ],
            rationale=list(bb.architecture.decisions) if bb.architecture else [],
            artifacts=[a.path for a in bb.all_artifacts()],
            risks=list(bb.validation.risks) if bb.validation else [],
            tradeoffs=list(bb.architecture.tradeoffs) if bb.architecture else [],
            validation=bb.validation.summary if bb.validation else "not run",
            assumptions=list(bb.assumptions),
            limitations=[
                "Offline deterministic engine covers known domains richly and unknown "
                "domains with a generic scaffold; it is not a general code synthesizer.",
                "Generated service targets clarity and the standard library over "
                "framework features (e.g. no async, no ORM).",
                "Human checkpoints are console-based in this prototype.",
            ],
        )
        bb.summary = summary

        # Also persist a human-readable Markdown summary as a first-class artifact.
        md = self._render(summary, bb)
        artifact = Artifact("ENGINEERING_SUMMARY.md", md, "docs")
        bb.docs.append(artifact)
        ctx.tools.artifacts.write(artifact)
        bb.log("summary", "engineering summary written")
        ctx.emit(self.name, "engineering summary written")

    @staticmethod
    def _render(s: EngineeringSummary, bb) -> str:
        def bullets(items):
            return "\n".join(f"- {i}" for i in items) if items else "- (none)"

        api_rows = ""
        if bb.architecture and bb.architecture.api:
            api_rows = "\n".join(
                f"| `{e.method}` | `{e.path}` | {e.summary} | {e.status} |"
                for e in bb.architecture.api
            )
            api_rows = ("\n\n## API Contract\n\n"
                        "| Method | Path | Summary | Status |\n"
                        "| --- | --- | --- | --- |\n" + api_rows)

        impact = ""
        if bb.impact:
            impact = "\n\n## Codebase Impact (brownfield)\n\n" + bullets(bb.impact)

        return f"""# Engineering Summary

**Requirement:** {s.requirement}
**Classification:** {s.kind}
**Validation:** {s.validation}

## Implementation Plan
{bullets(s.implementation_plan)}

## Rationale (key decisions)
{bullets(s.rationale)}
{api_rows}{impact}

## Generated Artifacts
{bullets(s.artifacts)}

## Risks
{bullets(s.risks)}

## Trade-offs
{bullets(s.tradeoffs)}

## Assumptions
{bullets(s.assumptions)}

## Limitations
{bullets(s.limitations)}
"""
