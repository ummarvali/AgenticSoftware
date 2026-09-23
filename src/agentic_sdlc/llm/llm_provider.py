"""LLM-first reasoning provider with a deterministic reliability fallback.

The **reasoning** stages (analysis, decomposition, design) are model-driven — this is
what makes the system a genuine LLM agent. Each call is guarded by:

* a per-call **timeout** and bounded **retries with backoff**,
* strict **JSON parsing + validation** of the model's output, and
* a **per-stage fallback** to :class:`DeterministicProvider` if the model errors or
  returns something invalid — so a run always completes.

The **generation** stages (code/tests/docs) intentionally use the verified knowledge
packs so the emitted URL shortener is guaranteed to compile and pass its tests. That is
a deliberate reliability decision (an SRE trades a little novelty for a demo that never
breaks), and it is documented as such — not a hidden limitation.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from agentic_sdlc.llm.base import ReasoningProvider
from agentic_sdlc.llm.client import CallRecord, LLMClient, MetricsCollector
from agentic_sdlc.llm.deterministic import DeterministicProvider
from agentic_sdlc.models import (
    AnalysisResult,
    Ambiguity,
    ApiEndpoint,
    Architecture,
    Artifact,
    Requirement,
    RequirementKind,
    Task,
    TaskGraph,
)

_KNOWN_CATEGORIES = {
    "design", "codebase_impact", "code", "tests", "docs", "validate", "summary",
}


class LLMProvider(ReasoningProvider):
    """Model-driven reasoning; deterministic fallback for reliability."""

    name = "llm"

    def __init__(
        self,
        client: LLMClient,
        *,
        timeout: float = 30.0,
        max_retries: int = 2,
        backoff_s: float = 0.0,
        metrics: MetricsCollector | None = None,
        fallback: ReasoningProvider | None = None,
    ) -> None:
        self._client = client
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff_s
        self.metrics = metrics or MetricsCollector()
        self._fallback = fallback or DeterministicProvider()

    # -- LLM call with retries + metrics ---------------------------------- #

    def _ask_json(self, stage: str, system: str, user: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            start = time.time()
            try:
                resp = self._client.complete(system, user, json_mode=True,
                                             timeout=self._timeout)
                data = json.loads(resp.text)
                self.metrics.record(CallRecord(
                    stage, resp.model, resp.prompt_tokens, resp.completion_tokens,
                    round(time.time() - start, 3),
                ))
                return data
            except Exception as exc:  # noqa: BLE001 - retry on any provider/JSON error
                last_error = exc
                self.metrics.record(CallRecord(
                    stage, getattr(self._client, "model", "unknown"), 0, 0,
                    round(time.time() - start, 3), error=str(exc)[:200],
                ))
                if attempt < self._max_retries and self._backoff:
                    time.sleep(self._backoff * attempt)
        raise RuntimeError(f"LLM stage '{stage}' failed after retries: {last_error}")

    def _with_fallback(self, stage: str, llm_fn: Callable[[], Any],
                       fallback_fn: Callable[[], Any]) -> Any:
        try:
            return llm_fn()
        except Exception as exc:  # noqa: BLE001 - degrade to deterministic
            self.metrics.record(CallRecord(
                stage, getattr(self._client, "model", "unknown"), 0, 0, 0.0,
                fallback=True, error=str(exc)[:200],
            ))
            return fallback_fn()

    # -- ReasoningProvider API -------------------------------------------- #

    def analyze_requirement(self, requirement: Requirement) -> AnalysisResult:
        def llm() -> AnalysisResult:
            system = (
                "You are a senior software engineer. Analyze the requirement and "
                "respond with STRICT JSON only, with keys: kind "
                "(greenfield|brownfield|ambiguous), intent, normalized_problem, "
                "functional_requirements (string[]), non_functional_requirements "
                "(string[]), ambiguities (array of {question, why_it_matters, "
                "default_assumption}), domain (short slug; use 'url_shortener' for "
                "URL-shortening work), confidence (number 0..1)."
            )
            user = requirement.text
            if requirement.repo_path:
                user += f"\n\n(An existing repository is provided at: {requirement.repo_path})"
            data = self._ask_json("analyze", system, user)
            return AnalysisResult(
                kind=RequirementKind(str(data.get("kind", "greenfield")).lower()),
                intent=data.get("intent", ""),
                normalized_problem=data.get("normalized_problem", ""),
                functional_requirements=list(data.get("functional_requirements", [])),
                non_functional_requirements=list(data.get("non_functional_requirements", [])),
                ambiguities=[Ambiguity(
                    question=a.get("question", ""),
                    why_it_matters=a.get("why_it_matters", ""),
                    default_assumption=a.get("default_assumption", ""),
                ) for a in data.get("ambiguities", [])],
                domain=data.get("domain", "generic") or "generic",
                confidence=float(data.get("confidence", 0.5)),
            )

        return self._with_fallback(
            "analyze", llm, lambda: self._fallback.analyze_requirement(requirement)
        )

    def decompose(self, analysis: AnalysisResult) -> TaskGraph:
        def llm() -> TaskGraph:
            system = (
                "Decompose the engineering problem into an executable task DAG. "
                "Respond with STRICT JSON only: {\"tasks\": [{id, title, description, "
                "depends_on (string[]), category, priority (int)}]}. category MUST be "
                "one of: design, codebase_impact, code, tests, docs, validate, summary. "
                "Include design, code, tests, docs, validate, summary; add "
                "codebase_impact only for brownfield. Ensure the graph is acyclic."
            )
            user = analysis.normalized_problem or analysis.intent
            data = self._ask_json("decompose", system, user)
            tasks = [Task(
                id=str(t["id"]),
                title=t.get("title", t["id"]),
                description=t.get("description", ""),
                depends_on=[str(d) for d in t.get("depends_on", [])],
                category=t.get("category", "generic"),
                priority=int(t.get("priority", 100)),
            ) for t in data.get("tasks", [])]
            graph = TaskGraph(tasks=tasks)
            # Validation guardrail: reject unknown categories or a cyclic/empty plan.
            if not tasks or any(t.category not in _KNOWN_CATEGORIES for t in tasks):
                raise ValueError("LLM plan has invalid categories")
            graph.validate_acyclic()
            return graph

        return self._with_fallback(
            "decompose", llm, lambda: self._fallback.decompose(analysis)
        )

    def design(self, analysis: AnalysisResult) -> Architecture:
        def llm() -> Architecture:
            system = (
                "Design the architecture. Respond with STRICT JSON only: {overview, "
                "components (string[]), data_model (string[]), api (array of {method, "
                "path, summary, request, response, status}), decisions (string[]), "
                "tradeoffs (string[])}."
            )
            data = self._ask_json("design", system, analysis.normalized_problem or analysis.intent)
            arch = Architecture(
                overview=data.get("overview", ""),
                components=list(data.get("components", [])),
                data_model=list(data.get("data_model", [])),
                api=[ApiEndpoint(
                    method=e.get("method", "GET"),
                    path=e.get("path", "/"),
                    summary=e.get("summary", ""),
                    request=e.get("request"),
                    response=e.get("response", ""),
                    status=int(e.get("status", 200)),
                ) for e in data.get("api", [])],
                decisions=list(data.get("decisions", [])),
                tradeoffs=list(data.get("tradeoffs", [])),
            )
            if not arch.components:
                raise ValueError("LLM design produced no components")
            return arch

        return self._with_fallback(
            "design", llm, lambda: self._fallback.design(analysis)
        )

    # Generation uses the verified packs for guaranteed-runnable output (reliability).
    def generate_code(self, analysis: AnalysisResult, architecture: Architecture) -> list[Artifact]:
        return self._fallback.generate_code(analysis, architecture)

    def generate_tests(
        self, analysis: AnalysisResult, architecture: Architecture, code: list[Artifact]
    ) -> list[Artifact]:
        return self._fallback.generate_tests(analysis, architecture, code)

    def generate_docs(self, analysis: AnalysisResult, architecture: Architecture) -> list[Artifact]:
        return self._fallback.generate_docs(analysis, architecture)
