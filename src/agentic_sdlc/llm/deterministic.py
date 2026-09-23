"""Offline, deterministic reasoning engine.

This provider makes the whole prototype reproducible and runnable with **zero
external dependencies or API keys**. It classifies the requirement, selects a
:class:`~agentic_sdlc.knowledge.base.KnowledgePack`, surfaces ambiguities with
explicit default assumptions, and produces a dependency-aware task graph plus all
engineering artifacts.

The same agents can instead call a live model via
:class:`~agentic_sdlc.llm.openai_provider.OpenAIProvider`; nothing else changes.
"""

from __future__ import annotations

from agentic_sdlc.knowledge import GenericPack
from agentic_sdlc.knowledge.base import KnowledgePack
from agentic_sdlc.knowledge.url_shortener import UrlShortenerPack
from agentic_sdlc.llm.base import ReasoningProvider
from agentic_sdlc.models import (
    AnalysisResult,
    Ambiguity,
    Architecture,
    Artifact,
    Requirement,
    RequirementKind,
    Task,
    TaskGraph,
)

# Registry of domain packs, tried in order. GenericPack is the explicit fallback.
_PACKS: list[type[KnowledgePack]] = [UrlShortenerPack]

_BROWNFIELD_MARKERS = (
    "existing", "refactor", "legacy", "migrate", "migration", "bug", "fix ",
    "the current", "already", "enhance", "extend the", "our system",
)
_VAGUE_ADJECTIVES = ("faster", "better", "slow", "nicer", "cleaner", "scalable", "modern")
_CONCRETE_NOUNS = (
    "api", "service", "endpoint", "database", "shortener", "feature", "test",
    "documentation", "schema", "pipeline", "auth", "cache",
)


class DeterministicProvider(ReasoningProvider):
    """Rule-based provider that generates real, reviewable engineering outputs."""

    name = "deterministic"

    # -- classification ---------------------------------------------------- #

    def _select_pack(self, text: str) -> KnowledgePack:
        for pack_cls in _PACKS:
            if pack_cls.matches(text):
                return pack_cls()
        return GenericPack()

    def _classify(self, requirement: Requirement) -> RequirementKind:
        low = requirement.text.lower()
        words = low.split()

        # Ambiguous: short + vague adjective + no concrete deliverable noun.
        if len(words) <= 7 and any(v in low for v in _VAGUE_ADJECTIVES):
            if not any(n in low for n in _CONCRETE_NOUNS):
                return RequirementKind.AMBIGUOUS

        # Brownfield: an explicit signal that something already exists.
        if requirement.repo_path or any(m in low for m in _BROWNFIELD_MARKERS):
            return RequirementKind.BROWNFIELD

        return RequirementKind.GREENFIELD

    def _detect_ambiguities(
        self, requirement: Requirement, kind: RequirementKind, domain: str
    ) -> list[Ambiguity]:
        if kind == RequirementKind.AMBIGUOUS:
            return [
                Ambiguity(
                    question="Which application/component is in scope?",
                    why_it_matters="Determines what code is even touched.",
                    default_assumption="The primary service in the provided repository.",
                ),
                Ambiguity(
                    question="What metric defines success (e.g. p95 latency target)?",
                    why_it_matters="Without a target, 'done' is undefined and unverifiable.",
                    default_assumption="Reduce p95 request latency by 30% under current load.",
                ),
                Ambiguity(
                    question="What is the acceptable scope of change (config vs rewrite)?",
                    why_it_matters="Bounds risk and effort.",
                    default_assumption="Non-breaking changes only; no public API changes.",
                ),
            ]
        if domain == "url_shortener":
            return [
                Ambiguity(
                    question="Are custom aliases and link expiry required?",
                    why_it_matters="Changes the API surface and data model.",
                    default_assumption="Support both as optional parameters.",
                ),
                Ambiguity(
                    question="What durability is required for links?",
                    why_it_matters="Drives the persistence choice.",
                    default_assumption="Provide SQLite durability with an in-memory option.",
                ),
                Ambiguity(
                    question="Is authentication required to create links?",
                    why_it_matters="Affects security posture and endpoints.",
                    default_assumption="Open creation for the prototype; auth is a documented extension.",
                ),
            ]
        return [
            Ambiguity(
                question="What are the concrete acceptance criteria?",
                why_it_matters="Defines what 'complete and correct' means.",
                default_assumption="Derive acceptance criteria from the stated functional goals.",
            ),
        ]

    # -- ReasoningProvider API -------------------------------------------- #

    def analyze_requirement(self, requirement: Requirement) -> AnalysisResult:
        pack = self._select_pack(requirement.text)
        kind = self._classify(requirement)
        ambiguities = self._detect_ambiguities(requirement, kind, pack.domain)

        functional = pack.functional_requirements() or [
            f"Implement the capability described: {requirement.text.strip()}",
        ]
        non_functional = pack.non_functional_requirements() or [
            "Maintainability: modular, tested, documented code.",
            "Reliability: validated inputs and predictable error handling.",
        ]

        if pack.domain == "url_shortener":
            intent = ("Deliver a production-ready URL shortener exposing shorten, "
                      "redirect, and analytics APIs backed by pluggable persistence.")
        elif kind == RequirementKind.AMBIGUOUS:
            intent = ("Clarify and then address an under-specified request; proceed on "
                      "documented default assumptions where a human does not intervene.")
        else:
            intent = f"Implement the described capability: {requirement.text.strip()}"

        normalized = self._normalize(requirement, kind, functional, non_functional)
        confidence = {
            "url_shortener": 0.92,
        }.get(pack.domain, 0.45 if kind == RequirementKind.AMBIGUOUS else 0.62)

        return AnalysisResult(
            kind=kind,
            intent=intent,
            normalized_problem=normalized,
            functional_requirements=functional,
            non_functional_requirements=non_functional,
            ambiguities=ambiguities,
            domain=pack.domain,
            confidence=confidence,
        )

    def _normalize(
        self,
        requirement: Requirement,
        kind: RequirementKind,
        functional: list[str],
        non_functional: list[str],
    ) -> str:
        fr = "\n".join(f"  - {f}" for f in functional)
        nfr = "\n".join(f"  - {n}" for n in non_functional)
        return (
            f"Problem ({kind.value}): {requirement.text.strip()}\n"
            f"Functional scope:\n{fr}\n"
            f"Quality attributes:\n{nfr}"
        )

    def decompose(self, analysis: AnalysisResult) -> TaskGraph:
        """Produce the executable build DAG.

        The graph is deliberately non-linear: documentation runs in parallel with
        testing, and brownfield work injects a codebase-impact task that the code
        task depends on. The orchestrator executes tasks by dependency level.
        """

        tasks: list[Task] = [
            Task("design", "Design architecture",
                 "Define components, data model, API contract, and trade-offs.",
                 depends_on=[], category="design", priority=10),
        ]
        code_deps = ["design"]

        if analysis.kind == RequirementKind.BROWNFIELD:
            tasks.append(
                Task("impact", "Analyze codebase impact",
                     "Identify impacted services, modules, APIs, and data flows.",
                     depends_on=[], category="codebase_impact", priority=10)
            )
            code_deps.append("impact")

        tasks += [
            Task("code", "Generate implementation",
                 "Emit production-quality code implementing the design.",
                 depends_on=code_deps, category="code", priority=20),
            Task("tests", "Generate tests",
                 "Emit unit and integration tests for the implementation.",
                 depends_on=["code"], category="tests", priority=30),
            Task("docs", "Generate documentation",
                 "Emit README and architecture docs.",
                 depends_on=["design"], category="docs", priority=30),
            Task("validate", "Validate artifacts",
                 "Compile code, run tests, check the contract, assess risks.",
                 depends_on=["tests", "docs"], category="validate", priority=40),
            Task("summary", "Write engineering summary",
                 "Produce the final structured engineering summary.",
                 depends_on=["validate"], category="summary", priority=50),
        ]
        graph = TaskGraph(tasks=tasks)
        graph.validate_acyclic()
        return graph

    def design(self, analysis: AnalysisResult) -> Architecture:
        return self._pack_for(analysis).architecture(analysis)

    def generate_code(self, analysis: AnalysisResult, architecture: Architecture) -> list[Artifact]:
        return self._pack_for(analysis).code(analysis, architecture)

    def generate_tests(
        self, analysis: AnalysisResult, architecture: Architecture, code: list[Artifact]
    ) -> list[Artifact]:
        return self._pack_for(analysis).tests(analysis, architecture, code)

    def generate_docs(self, analysis: AnalysisResult, architecture: Architecture) -> list[Artifact]:
        return self._pack_for(analysis).docs(analysis, architecture)

    def _pack_for(self, analysis: AnalysisResult) -> KnowledgePack:
        for pack_cls in _PACKS:
            if pack_cls.domain == analysis.domain:
                return pack_cls()
        return GenericPack()
