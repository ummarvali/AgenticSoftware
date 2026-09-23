"""Requirement analyst — turns raw text into a normalized engineering problem.

Runs during bootstrap (before the executable DAG exists) because its output — the
classification and normalized problem — is what the decomposer needs to build the
plan. It also converts each detected ambiguity into a recorded default assumption
so the run can proceed autonomously while remaining transparent about its choices.
"""

from __future__ import annotations

from agentic_sdlc.orchestrator.state import AgentContext


class RequirementAnalystAgent:
    name = "RequirementAnalyst"
    category = "analysis"

    def analyze(self, ctx: AgentContext) -> None:
        bb = ctx.blackboard
        ctx.emit(self.name, "interpreting requirement and identifying ambiguities")
        analysis = ctx.provider.analyze_requirement(bb.requirement)
        bb.analysis = analysis

        # Every ambiguity becomes an explicit, recorded assumption — this is how the
        # system stays autonomous without silently guessing.
        for amb in analysis.ambiguities:
            bb.assumptions.append(f"{amb.question} -> assumed: {amb.default_assumption}")

        bb.log(
            "analysis",
            f"classified as {analysis.kind.value} (domain={analysis.domain}, "
            f"confidence={analysis.confidence:.2f})",
            ambiguities=len(analysis.ambiguities),
        )
        ctx.emit(
            self.name,
            f"kind={analysis.kind.value} domain={analysis.domain} "
            f"({len(analysis.ambiguities)} ambiguities, "
            f"{len(analysis.functional_requirements)} FRs)",
        )
