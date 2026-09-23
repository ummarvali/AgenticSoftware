"""Knowledge packs — reusable, domain-specific generators.

A pack encapsulates everything the deterministic engine knows about one problem
domain (its architecture, code, tests, and docs). New domains are added by writing
a new pack and registering it, without touching agents or the orchestrator. The
mandatory URL-shortener use case is implemented as :mod:`.url_shortener`; anything
unrecognised falls back to :mod:`.generic`.
"""

from __future__ import annotations

import abc

from agentic_sdlc.models import AnalysisResult, Architecture, Artifact


class KnowledgePack(abc.ABC):
    """Domain generator contract used by the deterministic provider."""

    #: Stable identifier stored on the analysis and used to select this pack.
    domain: str = "generic"

    @classmethod
    @abc.abstractmethod
    def matches(cls, text: str) -> bool:
        """Return ``True`` when this pack should own the given requirement text."""

    @abc.abstractmethod
    def architecture(self, analysis: AnalysisResult) -> Architecture:
        ...

    @abc.abstractmethod
    def code(self, analysis: AnalysisResult, architecture: Architecture) -> list[Artifact]:
        ...

    @abc.abstractmethod
    def tests(
        self,
        analysis: AnalysisResult,
        architecture: Architecture,
        code: list[Artifact],
    ) -> list[Artifact]:
        ...

    @abc.abstractmethod
    def docs(self, analysis: AnalysisResult, architecture: Architecture) -> list[Artifact]:
        ...

    # Optional hooks a pack MAY override to enrich the shared analysis. Defaults
    # keep simple packs terse.
    def functional_requirements(self) -> list[str]:
        return []

    def non_functional_requirements(self) -> list[str]:
        return []
