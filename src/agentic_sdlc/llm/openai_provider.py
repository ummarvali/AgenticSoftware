"""Optional live-LLM backend.

This adapter shows the seam that lets the *same* agents and orchestrator run
against a real model for open-ended requirements the deterministic engine does not
cover. It is intentionally lazy: the ``openai`` package is only imported when this
provider is actually constructed, so the base prototype keeps zero dependencies.

For any capability the model is not asked to produce (code/tests/docs generation in
this prototype), it delegates to :class:`DeterministicProvider`, so a run never
half-completes. Wire real prompts into the marked methods to go fully live.
"""

from __future__ import annotations

import json
import os

from agentic_sdlc.llm.base import ReasoningProvider
from agentic_sdlc.llm.deterministic import DeterministicProvider
from agentic_sdlc.models import (
    AnalysisResult,
    Ambiguity,
    Architecture,
    Artifact,
    Requirement,
    RequirementKind,
    TaskGraph,
)


class OpenAIProvider(ReasoningProvider):
    """LLM-backed analysis with deterministic generation as a safety net."""

    name = "openai"

    def __init__(self, model: str = "gpt-4o-mini", api_key: str | None = None) -> None:
        try:
            from openai import OpenAI  # imported lazily; only needed when used
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "The 'openai' package is required for OpenAIProvider. "
                "Install it with: pip install -e \".[llm]\""
            ) from exc
        self._client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
        self._model = model
        # Deterministic engine backs the generation-heavy stages.
        self._fallback = DeterministicProvider()

    def analyze_requirement(self, requirement: Requirement) -> AnalysisResult:  # pragma: no cover
        prompt = (
            "Analyze this software requirement. Return strict JSON with keys "
            "kind (greenfield|brownfield|ambiguous), intent, normalized_problem, "
            "functional_requirements (list), non_functional_requirements (list), "
            "ambiguities (list of {question, why_it_matters, default_assumption}), "
            "domain, confidence (0-1).\n\nRequirement: " + requirement.text
        )
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        return AnalysisResult(
            kind=RequirementKind(data.get("kind", "greenfield")),
            intent=data.get("intent", ""),
            normalized_problem=data.get("normalized_problem", ""),
            functional_requirements=data.get("functional_requirements", []),
            non_functional_requirements=data.get("non_functional_requirements", []),
            ambiguities=[Ambiguity(**a) for a in data.get("ambiguities", [])],
            domain=data.get("domain", "generic"),
            confidence=float(data.get("confidence", 0.5)),
        )

    # Generation stages delegate to the deterministic engine so runs are complete
    # and reproducible even without bespoke prompts for every artifact type.
    def decompose(self, analysis: AnalysisResult) -> TaskGraph:
        return self._fallback.decompose(analysis)

    def design(self, analysis: AnalysisResult) -> Architecture:
        return self._fallback.design(analysis)

    def generate_code(self, analysis: AnalysisResult, architecture: Architecture) -> list[Artifact]:
        return self._fallback.generate_code(analysis, architecture)

    def generate_tests(
        self, analysis: AnalysisResult, architecture: Architecture, code: list[Artifact]
    ) -> list[Artifact]:
        return self._fallback.generate_tests(analysis, architecture, code)

    def generate_docs(self, analysis: AnalysisResult, architecture: Architecture) -> list[Artifact]:
        return self._fallback.generate_docs(analysis, architecture)
