"""Typed domain models shared by every agent and the orchestrator.

These dataclasses are the *contract* between pipeline stages. An agent never
reaches into another agent's internals; it only reads and writes these objects
through the shared :class:`~agentic_sdlc.orchestrator.state.Blackboard`. Keeping the
data model explicit is what makes cross-step coordination auditable and testable.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


class RequirementKind(str, enum.Enum):
    """How a requirement relates to existing code.

    The kind is inferred by the requirement analyst and changes which agents run
    (for example, brownfield work adds a codebase-impact analysis stage).
    """

    GREENFIELD = "greenfield"   # brand-new feature or system
    BROWNFIELD = "brownfield"   # change to something that already exists
    AMBIGUOUS = "ambiguous"     # intent is unclear and must be clarified first


@dataclass
class Requirement:
    """The raw input to the whole pipeline."""

    text: str
    # Optional pointer to an existing codebase for brownfield reasoning.
    repo_path: Optional[str] = None
    # Free-form context a caller may attach (tech constraints, SLAs, etc.).
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class Ambiguity:
    """A single unclear point plus the assumption used to proceed autonomously."""

    question: str            # what a human should decide
    why_it_matters: str      # the engineering impact of the answer
    default_assumption: str  # what the system assumes if nobody answers
    # True when no safe default exists: the answer would change what gets built. In the
    # GitHub pipeline a blocking question is asked on the issue before anything is built.
    blocking: bool = False


@dataclass
class AnalysisResult:
    """Normalized, machine-usable statement of the engineering problem."""

    kind: RequirementKind
    # One-sentence restatement of what the user actually wants.
    intent: str
    # The requirement rewritten as an unambiguous engineering problem.
    normalized_problem: str
    functional_requirements: list[str] = field(default_factory=list)
    non_functional_requirements: list[str] = field(default_factory=list)
    ambiguities: list[Ambiguity] = field(default_factory=list)
    # Domain tag (e.g. "url_shortener") used to select generation templates.
    domain: str = "generic"
    confidence: float = 0.0


@dataclass
class Task:
    """One node in the execution DAG produced by the decomposer."""

    id: str
    title: str
    description: str
    # IDs of tasks that must complete before this one may start.
    depends_on: list[str] = field(default_factory=list)
    # Which pipeline capability fulfils the task (architecture, code, test, docs...).
    category: str = "generic"
    # Best-effort ordering hint within a dependency level.
    priority: int = 100


@dataclass
class TaskGraph:
    """A directed acyclic graph of tasks with dependency-aware ordering."""

    tasks: list[Task] = field(default_factory=list)

    def by_id(self) -> dict[str, Task]:
        return {t.id: t for t in self.tasks}

    def validate_acyclic(self) -> None:
        """Raise ``ValueError`` if the graph has a cycle or a dangling dependency.

        This is a guardrail: a cyclic plan can never be executed, so we fail fast
        and loudly instead of hanging the orchestrator.
        """

        known = self.by_id()
        for task in self.tasks:
            for dep in task.depends_on:
                if dep not in known:
                    raise ValueError(f"Task '{task.id}' depends on unknown task '{dep}'")

        # Kahn's algorithm: if we cannot remove every node, a cycle remains.
        indegree = {t.id: 0 for t in self.tasks}
        for task in self.tasks:
            for dep in task.depends_on:
                indegree[task.id] += 1
        ready = [tid for tid, deg in indegree.items() if deg == 0]
        removed = 0
        dependents: dict[str, list[str]] = {t.id: [] for t in self.tasks}
        for task in self.tasks:
            for dep in task.depends_on:
                dependents[dep].append(task.id)
        while ready:
            node = ready.pop()
            removed += 1
            for child in dependents[node]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        if removed != len(self.tasks):
            raise ValueError("Task graph contains a cycle")

    def topological_levels(self) -> list[list[Task]]:
        """Group tasks into dependency levels.

        Tasks in the same level share no ordering constraint, which is exactly the
        information the orchestrator needs to coordinate (and could parallelise)
        work rather than running a flat, linear list.
        """

        self.validate_acyclic()
        known = self.by_id()
        indegree = {t.id: len(t.depends_on) for t in self.tasks}
        dependents: dict[str, list[str]] = {t.id: [] for t in self.tasks}
        for task in self.tasks:
            for dep in task.depends_on:
                dependents[dep].append(task.id)

        levels: list[list[Task]] = []
        ready = sorted(
            [tid for tid, deg in indegree.items() if deg == 0],
            key=lambda tid: (known[tid].priority, tid),
        )
        while ready:
            level = [known[tid] for tid in ready]
            levels.append(level)
            next_ready: list[str] = []
            for tid in ready:
                for child in dependents[tid]:
                    indegree[child] -= 1
                    if indegree[child] == 0:
                        next_ready.append(child)
            ready = sorted(next_ready, key=lambda tid: (known[tid].priority, tid))
        return levels


@dataclass
class ApiEndpoint:
    """A single row of the generated API contract."""

    method: str
    path: str
    summary: str
    request: Optional[str] = None
    response: str = ""
    status: int = 200


@dataclass
class Architecture:
    """The design the architect agent commits to before any code is written."""

    overview: str
    components: list[str] = field(default_factory=list)
    data_model: list[str] = field(default_factory=list)
    api: list[ApiEndpoint] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)   # ADR-style rationale
    tradeoffs: list[str] = field(default_factory=list)


@dataclass
class Artifact:
    """A generated file. ``path`` is relative to the run's artifact root."""

    path: str
    content: str
    kind: str = "code"   # code | test | docs | contract | config

    def language(self) -> str:
        if self.path.endswith(".py"):
            return "python"
        if self.path.endswith((".md", ".markdown")):
            return "markdown"
        if self.path.endswith((".yml", ".yaml")):
            return "yaml"
        if self.path.endswith(".json"):
            return "json"
        return "text"


@dataclass
class Check:
    """One validation check plus its outcome."""

    name: str
    passed: bool
    detail: str = ""


@dataclass
class ValidationReport:
    """Aggregated result of every guardrail run against the generated artifacts."""

    checks: list[Check] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def summary(self) -> str:
        ok = sum(1 for c in self.checks if c.passed)
        return f"{ok}/{len(self.checks)} checks passed"


@dataclass
class EngineeringSummary:
    """The human-facing wrap-up written at the end of a run."""

    requirement: str
    kind: str
    implementation_plan: list[str] = field(default_factory=list)
    rationale: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    tradeoffs: list[str] = field(default_factory=list)
    validation: str = ""
    validation_approach: list[str] = field(default_factory=list)
    validation_checks: list[dict] = field(default_factory=list)
    monitoring: dict = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    """Everything a single pipeline run produced — the public return value."""

    run_id: str
    requirement: Requirement
    analysis: Optional[AnalysisResult] = None
    task_graph: Optional[TaskGraph] = None
    architecture: Optional[Architecture] = None
    artifacts: list[Artifact] = field(default_factory=list)
    validation: Optional[ValidationReport] = None
    summary: Optional[EngineeringSummary] = None
    events: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    output_dir: str = ""
    awaiting_clarification: bool = False   # ask-first mode stopped with questions

    def to_dict(self) -> dict[str, Any]:
        """Serialise to plain dicts so the result can be dumped to JSON."""

        return {
            "run_id": self.run_id,
            "requirement": asdict(self.requirement),
            "analysis": asdict(self.analysis) if self.analysis else None,
            "task_graph": asdict(self.task_graph) if self.task_graph else None,
            "architecture": asdict(self.architecture) if self.architecture else None,
            "artifacts": [asdict(a) for a in self.artifacts],
            "validation": asdict(self.validation) if self.validation else None,
            "summary": asdict(self.summary) if self.summary else None,
            "events": self.events,
            "metrics": self.metrics,
            "output_dir": self.output_dir,
            "awaiting_clarification": self.awaiting_clarification,
        }
