"""Shared state — the blackboard agents coordinate through.

Every agent reads its inputs and writes its outputs here. Because the blackboard is
the *only* channel between agents, a run is fully inspectable: the event log plus
the accumulated artifacts explain exactly what happened and in what order. This is
what turns "a bunch of steps" into auditable, coordinated orchestration.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from agentic_sdlc.llm.base import ReasoningProvider
from agentic_sdlc.models import (
    AnalysisResult,
    Architecture,
    Artifact,
    EngineeringSummary,
    Requirement,
    ValidationReport,
)
from agentic_sdlc.tools import ToolBox


@dataclass
class Blackboard:
    """Mutable, append-only-ish store of everything the pipeline has produced."""

    requirement: Requirement
    analysis: Optional[AnalysisResult] = None
    architecture: Optional[Architecture] = None
    impact: list[str] = field(default_factory=list)
    code: list[Artifact] = field(default_factory=list)
    tests: list[Artifact] = field(default_factory=list)
    docs: list[Artifact] = field(default_factory=list)
    validation: Optional[ValidationReport] = None
    summary: Optional[EngineeringSummary] = None
    assumptions: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def all_artifacts(self) -> list[Artifact]:
        return [*self.code, *self.tests, *self.docs]

    def log(self, kind: str, message: str, **data: Any) -> dict[str, Any]:
        event = {"ts": round(time.time(), 3), "kind": kind, "message": message, **data}
        self.events.append(event)
        return event


@dataclass
class AgentContext:
    """Everything an agent is given to do its job — nothing global, nothing hidden."""

    blackboard: Blackboard
    provider: ReasoningProvider
    tools: ToolBox
    output_dir: str
    emit: Callable[[str, str], None]

    @property
    def requirement(self) -> Requirement:
        return self.blackboard.requirement
