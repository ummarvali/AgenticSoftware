"""The reasoning layer — the swappable "brain" behind the agents.

Every agent delegates its domain thinking to a :class:`ReasoningProvider`. This is
the single seam that lets the *same* orchestration run on:

* :class:`~agentic_sdlc.llm.deterministic.DeterministicProvider` — an offline,
  dependency-free engine that ships domain knowledge as code. It makes the
  prototype reproducible and runnable with zero API keys (crucial for grading).
* :class:`~agentic_sdlc.llm.llm_provider.LLMProvider` — a live LLM backend
  for open-ended requirements (optional, enabled only when configured).

Keeping the interface small and typed is what makes the two interchangeable.
"""

from __future__ import annotations

import abc

from agentic_sdlc.models import (
    AnalysisResult,
    Architecture,
    Artifact,
    Requirement,
    TaskGraph,
)


class ReasoningProvider(abc.ABC):
    """Capability contract that every backend (offline or live) must satisfy."""

    name: str = "abstract"

    @abc.abstractmethod
    def analyze_requirement(self, requirement: Requirement) -> AnalysisResult:
        """Interpret intent, classify the work, and surface ambiguities."""

    @abc.abstractmethod
    def decompose(self, analysis: AnalysisResult) -> TaskGraph:
        """Break the normalized problem into a dependency-aware task DAG."""

    @abc.abstractmethod
    def design(self, analysis: AnalysisResult) -> Architecture:
        """Produce components, data model, API contract, and trade-offs."""

    @abc.abstractmethod
    def generate_code(
        self, analysis: AnalysisResult, architecture: Architecture
    ) -> list[Artifact]:
        """Emit production-quality implementation files."""

    @abc.abstractmethod
    def generate_tests(
        self,
        analysis: AnalysisResult,
        architecture: Architecture,
        code: list[Artifact],
    ) -> list[Artifact]:
        """Emit unit and integration tests for the generated code."""

    @abc.abstractmethod
    def generate_docs(
        self, analysis: AnalysisResult, architecture: Architecture
    ) -> list[Artifact]:
        """Emit supporting documentation for the generated artifacts."""
