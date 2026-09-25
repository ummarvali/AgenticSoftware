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

        reused = {e.get("task") for e in bb.events if e["kind"] == "skip" and e.get("task")}
        plan = self._plan_as_executed(bb, reused)
        provider = ctx.provider.name
        llm_metrics = getattr(ctx.provider, "metrics", None)

        summary = EngineeringSummary(
            requirement=bb.requirement.text,
            kind=bb.analysis.kind.value,
            implementation_plan=plan,
            rationale=(list(bb.architecture.decisions) if bb.architecture else [])
                      + [e["message"] for e in bb.events if e["kind"] == "decision"],
            artifacts=[a.path for a in bb.all_artifacts()],
            risks=list(bb.validation.risks) if bb.validation else [],
            tradeoffs=list(bb.architecture.tradeoffs) if bb.architecture else [],
            validation=bb.validation.summary if bb.validation else "not run",
            validation_approach=[
                "Static safety (first, before anything runs): an AST scan rejects dangerous "
                "calls (eval/exec/os.system/shell=True/pickle, import aliases resolved), "
                "imports outside the standard library, hard-coded secrets and modules that "
                "shadow the standard library; code with a high-severity finding is not executed.",
                "Static: every generated .py file is compiled.",
                "Dynamic: the generated unit + integration suite is executed in an isolated "
                "interpreter (python -I) in a subprocess, with a timeout and a "
                "credential-scrubbed environment.",
                "Contract: an OpenAPI document must exist whenever the design exposes an API "
                "(existence is checked, not conformance).",
                "Documentation: README/architecture docs must be present.",
                "Feedback loop: repairable findings are fixed by the Repair agent and "
                "re-validated (bounded); compile failures halt for human attention.",
                "Human: a final acceptance gate reviews this report before the run is accepted.",
            ] + (["Model output: LLM-authored code was accepted only after passing a "
                  "sandbox compile+test gate (a rejected bundle gets one repair pass with "
                  "the sandbox output); otherwise the verified template was used."]
                 if provider == "llm" else []),
            validation_checks=[{"name": c.name, "passed": c.passed, "detail": c.detail}
                               for c in (bb.validation.checks if bb.validation else [])],
            monitoring=self._monitoring(bb, provider, llm_metrics),
            assumptions=list(bb.assumptions),
            limitations=self._limitations(provider, llm_metrics) + self._scope_limitations(bb),
        )
        bb.summary = summary

        # Also persist a human-readable Markdown summary as a first-class artifact.
        md = self._render(summary, bb)
        artifact = Artifact("ENGINEERING_SUMMARY.md", md, "docs")
        bb.merge(bb.docs, [artifact])
        ctx.tools.artifacts.write(artifact)
        bb.log("summary", "engineering summary written")
        ctx.emit(self.name, "engineering summary written")


    @staticmethod
    def _plan_as_executed(bb, reused: set) -> list[str]:
        """The approved DAG, level by level, marking which tasks executed vs reused."""

        if not bb.task_graph:
            return ["(no plan recorded)"]
        lines = []
        for i, level in enumerate(bb.task_graph.topological_levels()):
            items = ", ".join(
                f"{t.id} ({t.category}{', reused' if t.id in reused else ''})" for t in level
            )
            lines.append(f"Level {i}: {items}")
        return lines

    @staticmethod
    def _monitoring(bb, provider: str, llm) -> dict:
        def count(kind):
            return sum(1 for e in bb.events if e["kind"] == kind)
        m = {
            "provider": provider,
            "tasks_completed": count("task"),
            "retries": count("task_error"),
            "repairs": count("repair"),
            "degradations": count("degrade"),
            "parallel_levels": count("parallel"),
            "reused_tasks": count("skip"),
            "human_gates_passed_before_summary": count("gate"),
        }
        if llm is not None and llm.calls:
            m["llm_calls"] = len(llm.calls)
            m["llm_tokens"] = llm.total_tokens
            m["llm_calls"] = llm.api_calls
            m["llm_est_cost_usd"] = (round(llm.est_cost_usd, 4) if llm.est_cost_usd is not None
                                     else "n/a (model price unknown)")
            m["llm_fallbacks"] = [f"{c.stage}: {c.error}" for c in llm.calls if c.fallback]
        return m

    @staticmethod
    def _scope_limitations(bb) -> list[str]:
        """State plainly where the validated slice is narrower than the design."""

        out: list[str] = []
        if bb.change_mode:
            out.append(
                "Change mode: the repository was only read; the proposal is the change set in "
                "this folder plus CHANGES.diff, validated on a throwaway copy of the repository "
                "with the change applied (its tests/ suite plus the new tests). The model sees a "
                "relevance-ranked subset of the repository (~60 KB); file deletions are not proposed.")
        implemented, design_only = bb.endpoint_coverage()
        if design_only:
            out.append(
                f"Design ↔ implementation: {len(implemented)}/{len(implemented) + len(design_only)} "
                f"designed endpoints are served by the generated slice; design-only: "
                + ", ".join(e.path for e in design_only) + "."
            )
        low = bb.requirement.text.lower()
        for lang in ("golang", " go ", "java", "kotlin", "typescript", "node", "rust", "c#", ".net", "c++"):
            if lang in f" {low} ":
                out.append(
                    "The requirement names a non-Python target; the design records that target, "
                    "but the validated prototype slice is Python (standard library) because that is "
                    "what the compile+test gate can execute. Other languages need a runner + prompt "
                    "(`CodeRunner`, `prompts/codegen.md`)."
                )
                break
        return out

    @staticmethod
    def _limitations(provider: str, llm) -> list[str]:
        common = [
            "Generated service targets clarity and the standard library over "
            "framework features (e.g. no async, no ORM).",
            "Human checkpoints are console-based in this prototype.",
            "The validation sandbox is an isolated-mode subprocess with a timeout and a "
            "scrubbed environment, not a network-isolated container or separate OS user.",
        ]
        if provider == "llm":
            fb = [c for c in (llm.calls if llm else []) if c.fallback]
            note = ("Reasoning and code authoring were model-driven; "
                    + (f"{len(fb)} stage(s) fell back to the deterministic engine "
                       f"({', '.join(c.stage for c in fb)})." if fb
                       else "no stage needed the deterministic fallback."))
            fidelity = ("The design decisions under Rationale describe the model's target "
                        "design. What is verified for the generated slice is: the endpoints "
                        "marked 'yes' in the coverage table exist in the code, the code "
                        "compiles, passes the static scan, and passes the model's own tests. "
                        "Individual decisions (e.g. an async queue, a required header) are not "
                        "checked against the code; a critic agent that does so is the next step. "
                        "The API contract and README are synthesized from the design by the "
                        "Repair agent when the model's bundle does not include them.")
            return [note, fidelity] + common
        return ["Offline deterministic engine covers known domains richly and unknown "
                "domains with a generic scaffold; it is not a general code synthesizer."] + common

    @staticmethod
    def _render(s: EngineeringSummary, bb) -> str:
        def bullets(items):
            return "\n".join(f"- {i}" for i in items) if items else "- (none)"

        api_rows = ""
        if bb.architecture and bb.architecture.api:
            implemented, design_only = bb.endpoint_coverage()
            impl = {id(e) for e in implemented}
            api_rows = "\n".join(
                f"| `{e.method}` | `{e.path}` | {e.summary} | {e.status} | "
                f"{'yes' if id(e) in impl else 'design-only'} |"
                for e in bb.architecture.api
            )
            api_rows = ("\n\n## API Contract (design) and implementation coverage\n\n"
                        "| Method | Path | Summary | Status | In generated slice |\n"
                        "| --- | --- | --- | --- | --- |\n" + api_rows)

        checks = "\n".join(
            f"| {c['name']} | {'PASS' if c['passed'] else 'FAIL'} | "
            f"{str(c['detail']).replace(chr(10), ' ').replace('|', '/')[:160]} |"
            for c in s.validation_checks
        ) or "| (none) | - | - |"

        impact = ""
        if bb.impact:
            impact = "\n\n## Codebase Impact (brownfield)\n\n" + bullets(bb.impact)
        if bb.change_mode:
            from agentic_sdlc.tools import repo as repo_tool
            changes = [a for a in bb.all_artifacts()
                       if a.kind != "change" and a.path != "ENGINEERING_SUMMARY.md"]
            rows = "\n".join(f"| `{p}` | {kind} | +{a} / -{r} |"
                             for p, kind, a, r in repo_tool.diff_stats(bb.repo_files, changes))
            impact += ("\n\n## Proposed change set (against the existing repository)\n\n"
                       + (bb.change_summary + "\n\n" if bb.change_summary else "")
                       + ("| File | Change | Lines |\n| --- | --- | --- |\n" + rows
                          + "\n\nFull patch: `CHANGES.diff`. The repository itself was not modified."
                          if rows else "No change set was produced (see Validation)."))

        return f"""# Engineering Summary

**Requirement:** {s.requirement}
**Classification:** {s.kind}
**Validation:** {s.validation}

## Implementation Plan
{bullets(s.implementation_plan)}

## Rationale (key decisions & agent decision log)
{bullets(s.rationale)}
{api_rows}{impact}

## Generated Artifacts
{bullets(s.artifacts)}

## Validation

**Result:** {s.validation}

| Check | Result | Detail |
| --- | --- | --- |
{checks}

Approach:
{bullets(s.validation_approach)}

## Run Monitoring
{bullets(f"{k}: {v}" for k, v in s.monitoring.items())}

## Risks
{bullets(s.risks)}

## Trade-offs
{bullets(s.tradeoffs)}

## Assumptions
{bullets(s.assumptions)}

## Limitations
{bullets(s.limitations)}
"""
