"""Shared state — the blackboard agents coordinate through.

Every agent reads its inputs and writes its outputs here. Because the blackboard is
the *only* channel between agents, a run is fully inspectable: the event log plus
the accumulated artifacts explain exactly what happened and in what order. This is
what turns "a bunch of steps" into auditable, coordinated orchestration.
"""

from __future__ import annotations

import hashlib
import threading
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
    TaskGraph,
    ValidationReport,
)
from agentic_sdlc.tools import ToolBox


@dataclass
class Blackboard:
    """Mutable, append-only-ish store of everything the pipeline has produced."""

    requirement: Requirement
    analysis: Optional[AnalysisResult] = None
    architecture: Optional[Architecture] = None
    task_graph: Optional[TaskGraph] = None   # the human-approved plan
    impact: list[str] = field(default_factory=list)
    # Brownfield with --repo: a read-only snapshot of the repository (path -> text) and
    # the fact that this run proposes a *change set* against it, not a new project.
    repo_files: dict[str, str] = field(default_factory=dict)
    change_mode: bool = False
    change_summary: str = ""
    code: list[Artifact] = field(default_factory=list)
    tests: list[Artifact] = field(default_factory=list)
    docs: list[Artifact] = field(default_factory=list)
    validation: Optional[ValidationReport] = None
    validated_fingerprint: str = ""   # artifact set the current report was computed on
    docs_attempted: bool = False        # a docs stage ran (even if it produced nothing)
    summary: Optional[EngineeringSummary] = None
    assumptions: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    # Agents in the same DAG level may run concurrently; the event log is the one
    # structure every agent appends to, so it is guarded. (Agents write disjoint
    # blackboard sections — code vs docs — so those need no further locking.)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    def all_artifacts(self) -> list[Artifact]:
        return [*self.code, *self.tests, *self.docs]

    def merge(self, section: list[Artifact], new: list[Artifact]) -> list[Artifact]:
        """Add artifacts to a section, replacing any existing one with the same path.

        A fine-grained plan (several `code` tasks) or a repair pass must never leave
        duplicate files on the blackboard — the artifact set is a *set*, keyed by path.
        Returns the artifacts that were actually new or changed."""

        with self._lock:
            index = {a.path: i for i, a in enumerate(section)}
            changed: list[Artifact] = []
            for art in new:
                i = index.get(art.path)
                if i is None:
                    index[art.path] = len(section)
                    section.append(art)
                    changed.append(art)
                elif section[i].content != art.content:
                    section[i] = art
                    changed.append(art)
            return changed

    def endpoint_coverage(self) -> tuple[list, list]:
        """Split the design's endpoints into (implemented, design_only) by looking for each
        path's literal prefix in the generated code. Cheap, honest, and enough to stop a
        contract from promising endpoints the prototype slice does not serve."""

        api = list(self.architecture.api) if self.architecture else []
        blob = "\n".join(a.content for a in self.code if a.path.endswith(".py"))
        if self.change_mode:   # a change is served together with the code it changes
            blob += "\n" + "\n".join(t for p, t in self.repo_files.items() if p.endswith(".py"))
        implemented, design_only = [], []
        for e in api:
            prefix = e.path.split("{")[0].rstrip("/") or "/"
            (implemented if prefix and prefix in blob else design_only).append(e)
        return implemented, design_only

    def fingerprint(self) -> str:
        """Content hash of the current artifact set, for idempotence."""

        h = hashlib.sha256()
        for a in sorted(self.all_artifacts(), key=lambda a: a.path):
            h.update(a.path.encode("utf-8") + b"\0" + a.content.encode("utf-8") + b"\0")
        return h.hexdigest()

    def log(self, kind: str, message: str, **data: Any) -> dict[str, Any]:
        event = {"ts": round(time.time(), 3), "kind": kind, "message": message, **data}
        with self._lock:
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
