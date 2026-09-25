"""The orchestrator — coordinates agents into an auditable, recoverable workflow.

Control flow:

1. **Bootstrap** — the analyst normalizes the requirement; a human *clarification*
   gate reviews ambiguities/assumptions; the decomposer builds the task DAG; a
   human *plan* gate approves it.
2. **Execute** — tasks run **by dependency level** (not a flat list), each dispatched
   to the agent registered for its category, all coordinating through one blackboard.
   Every task is wrapped in retry-with-backoff and recovery.
3. **Accept** — a final human gate reviews the validation report before the run is
   finalized and written to disk.

This is deliberately more than linear execution: levels expose parallelism, tasks
carry real dependencies, agents share state, and failures are retried or degraded.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from agentic_sdlc.agents import (
    DAG_AGENTS,
    RequirementAnalystAgent,
    TaskDecomposerAgent,
)
from agentic_sdlc.hitl import ApprovalGate, AutoApprove, ConsoleApproval, Decision
from agentic_sdlc.llm import get_provider
from agentic_sdlc.llm.base import ReasoningProvider
from agentic_sdlc.models import Requirement, RunResult, Task, TaskGraph
from agentic_sdlc.orchestrator.state import AgentContext, Blackboard
from agentic_sdlc.tools import ArtifactStore, CodeRunner, ToolBox

# Categories the run can complete WITHOUT — a persistent failure here degrades the
# run (logged + skipped) instead of aborting it. Everything else is required.
_OPTIONAL_CATEGORIES = {"docs", "codebase_impact", "repair"}


class PipelineHalted(Exception):
    """Raised when a human rejects a checkpoint or a required task cannot recover."""


@dataclass
class OrchestratorConfig:
    provider: str = "auto"
    interactive: bool = False
    output_root: str = "runs"
    max_attempts: int = 2          # attempts per task before giving up
    backoff_seconds: float = 0.0   # delay between retries (0 keeps tests fast)
    max_repair_iterations: int = 1  # validation-driven self-correction rounds
    parallel: bool = True          # run independent tasks of a DAG level concurrently
    verbose: bool = True
    # Fault injection for demonstrating recovery: {category: times_to_fail}.
    inject_fault: dict[str, int] = field(default_factory=dict)


class Orchestrator:
    """Owns a single run from requirement to finalized engineering outcome."""

    def __init__(
        self,
        config: Optional[OrchestratorConfig] = None,
        gate: Optional[ApprovalGate] = None,
        provider: Optional[ReasoningProvider] = None,
    ) -> None:
        self.config = config or OrchestratorConfig()
        self.provider = provider or get_provider(self.config.provider)
        self.gate = gate or (ConsoleApproval() if self.config.interactive else AutoApprove())
        self._fault_budget = dict(self.config.inject_fault)
        self._fault_lock = threading.Lock()

    # -- public API -------------------------------------------------------- #

    def run(self, requirement: Requirement | str) -> RunResult:
        if isinstance(requirement, str):
            requirement = Requirement(text=requirement)

        run_id = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        started = time.time()
        output_dir = Path(self.config.output_root) / run_id / "artifacts"
        tools = ToolBox(artifacts=ArtifactStore(output_dir), runner=CodeRunner())
        bb = Blackboard(requirement=requirement)
        ctx = AgentContext(
            blackboard=bb,
            provider=self.provider,
            tools=tools,
            output_dir=str(output_dir),
            emit=self._make_emitter(bb),
        )

        result = RunResult(run_id=run_id, requirement=requirement, output_dir=str(output_dir))
        self._say(f"[orchestrator] provider: {self.provider.name}")
        self._say("[orchestrator] human gates: "
                  + ("console — you will be asked at each checkpoint"
                     if self.config.interactive else
                     "auto-approve (non-interactive; add --interactive to review each checkpoint)"))
        try:
            self._bootstrap(ctx)
            graph = self._plan(ctx)
            result.task_graph = graph
            self._execute(ctx, graph)
            self._repair_loop(ctx)
            self._accept(ctx)
        except PipelineHalted as exc:
            bb.log("halted", str(exc))
            self._say(f"\n[orchestrator] HALTED: {exc}")

        # Assemble and persist the result regardless of halt, so partial runs are
        # still inspectable.
        result.analysis = bb.analysis
        result.architecture = bb.architecture
        result.artifacts = bb.all_artifacts()
        result.validation = bb.validation
        result.summary = bb.summary
        result.events = bb.events
        # Always-on run observability (works offline). LLM usage is merged when present.
        run_metrics = self._run_metrics(bb, round(time.time() - started, 3))
        result.metrics = {"run": run_metrics}
        llm = getattr(self.provider, "metrics", None)
        if llm is not None and llm.calls:
            result.metrics["llm"] = llm.to_dict()
        self._print_observability(run_metrics, llm)
        self._persist(result)
        return result

    # -- phases ------------------------------------------------------------ #

    def _bootstrap(self, ctx: AgentContext) -> None:
        RequirementAnalystAgent().analyze(ctx)
        analysis = ctx.blackboard.analysis
        assert analysis is not None
        summary = (
            f"Kind: {analysis.kind.value} | domain: {analysis.domain} | "
            f"confidence: {analysis.confidence:.2f}\n"
            f"Intent: {analysis.intent}\n"
            f"Ambiguities & default assumptions:\n"
            + "\n".join(f"  - {a}" for a in ctx.blackboard.assumptions)
        )
        decision = self.gate.review("Requirement clarification", summary)
        ctx.blackboard.log("gate", f"clarification: {decision.note}", approved=decision.approved)
        self._say(f"[gate 1/3] requirement clarification: {decision.note}")
        if not decision.approved:
            raise PipelineHalted("clarification checkpoint rejected")

    def _plan(self, ctx: AgentContext) -> TaskGraph:
        graph = TaskDecomposerAgent().build(ctx)
        ctx.blackboard.task_graph = graph
        levels = graph.topological_levels()
        rendered = "\n".join(
            f"  level {i}: " + ", ".join(f"{t.id}({t.category})" for t in lvl)
            for i, lvl in enumerate(levels)
        )
        decision = self.gate.review("Execution plan approval", rendered)
        ctx.blackboard.log("gate", f"plan: {decision.note}", approved=decision.approved)
        self._say(f"[gate 2/3] execution plan: {decision.note}")
        if not decision.approved:
            raise PipelineHalted("plan checkpoint rejected")
        return graph

    def _execute(self, ctx: AgentContext, graph: TaskGraph) -> None:
        """Walk the DAG level by level; tasks inside a level have no mutual
        dependency, so they run concurrently (a thread per task) unless
        ``config.parallel`` is off. Level N+1 never starts before level N completes,
        which is exactly the ordering the dependency graph guarantees."""

        for level_no, level in enumerate(graph.topological_levels()):
            concurrent = self.config.parallel and len(level) > 1
            self._say(f"\n[orchestrator] --- level {level_no}: "
                      f"{', '.join(t.id for t in level)}"
                      f"{' (parallel)' if concurrent else ''} ---")
            if not concurrent:
                for task in level:
                    self._run_task(ctx, task)
                continue
            ctx.blackboard.log("parallel", f"level {level_no}: running "
                               f"{len(level)} tasks concurrently",
                               tasks=[t.id for t in level])
            with ThreadPoolExecutor(max_workers=len(level),
                                    thread_name_prefix=f"level{level_no}") as pool:
                futures = {pool.submit(self._run_task, ctx, task): task for task in level}
                halt: Optional[PipelineHalted] = None
                for fut, task in futures.items():
                    try:
                        fut.result()          # waits for every task; surfaces exceptions
                    except PipelineHalted as exc:
                        halt = halt or exc    # let siblings finish, then halt once
            if halt is not None:
                raise halt

    def _repair_loop(self, ctx: AgentContext) -> None:
        """Agent-driven recovery: fix repairable validation findings and re-check.

        This is the feedback loop that makes the workflow non-linear — validation
        results flow *back* into generation. It is bounded by ``max_repair_iterations``
        so it can never spin, and it only acts on findings the RepairAgent understands.
        """

        from agentic_sdlc.agents.validator import REPAIRABLE_CHECKS

        for i in range(self.config.max_repair_iterations):
            report = ctx.blackboard.validation
            if not report or report.passed:
                return
            repairable = [c.name for c in report.checks
                          if not c.passed and c.name in REPAIRABLE_CHECKS]
            if not repairable:
                return
            self._say(f"\n[orchestrator] --- repair iteration {i + 1}: "
                      f"{', '.join(repairable)} ---")
            self._run_task(ctx, Task("repair", "Repair findings", "", category="repair"))
            self._run_task(ctx, Task("revalidate", "Re-validate", "", category="validate"))
            self._run_task(ctx, Task("resummary", "Re-summarize", "", category="summary"))

    def _accept(self, ctx: AgentContext) -> None:
        bb = ctx.blackboard
        report = bb.validation
        summary = (
            f"Validation: {report.summary if report else 'n/a'}\n"
            f"Artifacts: {len(bb.all_artifacts())}\n"
            + ("Failing checks: "
               + ", ".join(c.name for c in report.checks if not c.passed)
               if report and not report.passed else "All checks passed.")
        )
        if isinstance(self.gate, AutoApprove) and report and not report.passed:
            # Autonomy may proceed on documented assumptions, but it never signs off on
            # failing work: that decision is reserved for a human.
            decision = Decision(False, "not accepted — validation failed; non-interactive "
                                       "mode never accepts a failing report (human review required)")
        else:
            decision = self.gate.review("Final acceptance", summary)
        bb.log("gate", f"acceptance: {decision.note}", approved=decision.approved)
        self._say(f"[gate 3/3] final acceptance: {decision.note}")
        if not decision.approved:
            raise PipelineHalted("final acceptance rejected")

    # -- task execution with retry / recovery ------------------------------ #

    def _run_task(self, ctx: AgentContext, task: Task) -> None:
        agent = DAG_AGENTS.get(task.category)
        if agent is None:
            ctx.blackboard.log("skip", f"no agent for category '{task.category}'")
            return

        last_error: Optional[Exception] = None
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                self._maybe_inject_fault(task.category)
                agent.run(ctx, task)
                ctx.blackboard.log("task", f"{task.id} ok", attempt=attempt)
                return
            except Exception as exc:  # noqa: BLE001 - orchestrator is the safety net
                last_error = exc
                ctx.blackboard.log(
                    "task_error", f"{task.id} failed: {exc}",
                    attempt=attempt, category=task.category,
                )
                self._say(f"[orchestrator] task '{task.id}' attempt {attempt} "
                          f"failed: {exc}")
                if attempt < self.config.max_attempts and self.config.backoff_seconds:
                    time.sleep(self.config.backoff_seconds * attempt)

        # Exhausted retries: degrade optional tasks, halt on required ones.
        if task.category in _OPTIONAL_CATEGORIES:
            ctx.blackboard.log("degrade", f"{task.id} skipped after retries: {last_error}")
            self._say(f"[orchestrator] optional task '{task.id}' skipped (recovered).")
            return
        raise PipelineHalted(f"required task '{task.id}' failed: {last_error}")

    def _maybe_inject_fault(self, category: str) -> None:
        with self._fault_lock:               # budget is shared across level threads
            remaining = self._fault_budget.get(category, 0)
            if remaining <= 0:
                return
            self._fault_budget[category] = remaining - 1
        raise RuntimeError(f"injected fault for '{category}' "
                           f"(remaining {remaining - 1})")

    # -- helpers ----------------------------------------------------------- #

    def _make_emitter(self, bb: Blackboard):
        def emit(agent: str, message: str) -> None:
            bb.log("agent", message, agent=agent)
            self._say(f"[{agent}] {message}")
        return emit

    def _say(self, message: str) -> None:
        if not self.config.verbose:
            return
        try:
            print(message)
        except UnicodeEncodeError:
            # Last-resort guard for consoles without UTF-8 (e.g. redirected on Windows).
            print(message.encode("ascii", "replace").decode("ascii"))

    @staticmethod
    def _run_metrics(bb: Blackboard, duration_s: float) -> dict:
        """Derive always-on monitoring metrics from the run's event log."""

        def count(kind: str) -> int:
            return sum(1 for e in bb.events if e["kind"] == kind)

        report = bb.validation
        return {
            "duration_s": duration_s,
            "tasks_ok": count("task"),
            "retries": count("task_error"),
            "repairs": count("repair"),
            "degradations": count("degrade"),
            "parallel_levels": count("parallel"),
            "reused": count("skip"),
            "decisions": count("decision"),
            "gates": count("gate"),
            "halted": count("halted") > 0,
            "artifacts": len(bb.all_artifacts()),
            "validation": report.summary if report else "n/a",
            "validation_passed": bool(report and report.passed),
        }

    def _print_observability(self, run: dict, llm) -> None:
        self._say(
            f"[observability] duration={run['duration_s']}s | tasks={run['tasks_ok']} "
            f"retries={run['retries']} repairs={run['repairs']} "
            f"degraded={run['degradations']} parallel_levels={run['parallel_levels']} "
            f"reused={run['reused']} gates={run['gates']} | "
            f"provider={self.provider.name} | validation={run['validation']}"
        )
        if llm is not None and llm.calls:
            self._say(f"[observability] LLM: {llm.summary()}")

    def _persist(self, result: RunResult) -> None:
        run_dir = Path(self.config.output_root) / result.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "result.json").write_text(
            json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8"
        )


def run_pipeline(requirement: Requirement | str, **kwargs) -> RunResult:
    """Convenience wrapper: build an orchestrator from kwargs and run once."""

    return Orchestrator(OrchestratorConfig(**kwargs)).run(requirement)
