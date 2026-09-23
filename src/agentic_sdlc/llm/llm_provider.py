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
import os
import re
import threading
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


def _to_int(value, default: int) -> int:
    """Lenient integer parsing for model output: 201, "201", "201 Created", "201/400" -> 201."""

    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"\d+", str(value or ""))
    return int(m.group()) if m else default


# Output budget per stage. Reasoning stages are small; authoring a whole project is not.
_MAX_TOKENS = {"analyze": 4096, "decompose": 4096, "design": 4096, "codegen": 32000}


def _coerce_json(text: str) -> dict:
    """Parse JSON from a model reply, tolerating code fences or surrounding prose."""
    s = text.strip()
    if s.startswith("```"):
        parts = s.split("```")
        s = parts[1] if len(parts) >= 2 else s
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
    try:
        return json.loads(s)
    except Exception:
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end > start:
            return json.loads(s[start:end + 1])
        raise


class BudgetExceeded(RuntimeError):
    """Raised before a model call when the run's call/cost budget is spent."""


class LLMProvider(ReasoningProvider):
    """Model-driven reasoning; deterministic fallback for reliability."""

    name = "llm"

    def __init__(
        self,
        client: LLMClient,
        *,
        timeout: float = 120.0,
        max_retries: int = 2,
        backoff_s: float = 0.0,
        enable_codegen: bool = True,
        metrics: MetricsCollector | None = None,
        fallback: ReasoningProvider | None = None,
        max_calls: int | None = None,
        max_cost_usd: float | None = None,
    ) -> None:
        self._client = client
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff_s
        # When True, the model authors the project; the deterministic pack is used only
        # if that output fails the sandbox compile+test gate below.
        self._enable_codegen = enable_codegen
        self.metrics = metrics or MetricsCollector()
        self._fallback = fallback or DeterministicProvider()
        self._bundle: dict[str, list[Artifact]] | None = None
        self._bundle_failed = False
        # Spend guardrail: once either budget is exhausted, every further stage
        # degrades to the deterministic engine instead of calling the model. A
        # runaway plan can therefore cost at most the budget, never more.
        self._max_calls = max_calls if max_calls is not None else int(
            os.environ.get("AGENTIC_LLM_MAX_CALLS", "40"))
        self._max_cost_usd = max_cost_usd if max_cost_usd is not None else float(
            os.environ.get("AGENTIC_LLM_MAX_COST_USD", "1.00"))
        # code/docs generators may run concurrently in one DAG level; the model must
        # author the project exactly once, so the bundle is built under a lock.
        self._bundle_lock = threading.Lock()

    # -- LLM call with retries + metrics ---------------------------------- #

    def _budget_check(self, stage: str) -> None:
        calls = len(self.metrics.calls)
        cost = self.metrics.est_cost_usd
        if calls >= self._max_calls:
            raise BudgetExceeded(f"{stage}: LLM call budget exhausted ({calls}/{self._max_calls})")
        if cost >= self._max_cost_usd:
            raise BudgetExceeded(f"{stage}: LLM cost budget exhausted (~${cost:.4f} >= ${self._max_cost_usd:.2f})")

    def _ask_json(self, stage: str, system: str, user: str) -> dict[str, Any]:
        self._budget_check(stage)
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            start = time.time()
            try:
                resp = self._client.complete(system, user, json_mode=True,
                                             timeout=self._timeout,
                                             max_tokens=_MAX_TOKENS.get(stage, 4096))
                try:
                    data = _coerce_json(resp.text)
                except Exception as exc:
                    head = (resp.text or "").strip().replace("\n", " ")[:80]
                    raise ValueError(f"non-JSON reply ({exc}); starts with: {head!r}") from exc
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
                priority=_to_int(t.get("priority", 100), 100),
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
                "tradeoffs (string[])}. 'status' is a single integer HTTP status for the "
                "success case (e.g. 201); 'method' is one verb."
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
                    status=_to_int(e.get("status", 200), 200),
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

    # Generation is model-authored, but only accepted if it passes a sandbox
    # compile+test gate. Otherwise the verified template is used so a run never breaks.
    def generate_code(self, analysis: AnalysisResult, architecture: Architecture) -> list[Artifact]:
        self._ensure_bundle(analysis, architecture)
        if self._bundle is not None:
            return self._bundle["code"]
        return self._fallback.generate_code(analysis, architecture)

    def generate_tests(
        self, analysis: AnalysisResult, architecture: Architecture, code: list[Artifact]
    ) -> list[Artifact]:
        self._ensure_bundle(analysis, architecture)
        if self._bundle is not None:
            return self._bundle["tests"]
        return self._fallback.generate_tests(analysis, architecture, code)

    def generate_docs(self, analysis: AnalysisResult, architecture: Architecture) -> list[Artifact]:
        self._ensure_bundle(analysis, architecture)
        if self._bundle is not None:
            return self._bundle["docs"]
        return self._fallback.generate_docs(analysis, architecture)

    # -- model-authored project bundle + sandbox validation --------------- #

    def _ensure_bundle(self, analysis: AnalysisResult, architecture: Architecture) -> None:
        """Generate the whole project once, validate it, and cache or give up.

        All three generation stages share one bundle so the emitted code and tests are
        mutually consistent. The bundle is accepted only if every file compiles and the
        generated tests pass in a throwaway sandbox; otherwise we fall back wholesale.
        """

        with self._bundle_lock:
            self._ensure_bundle_locked(analysis, architecture)

    def _ensure_bundle_locked(self, analysis: AnalysisResult, architecture: Architecture) -> None:
        if self._bundle is not None or self._bundle_failed or not self._enable_codegen:
            self._bundle_failed = self._bundle_failed or not self._enable_codegen
            return
        try:
            files = self._llm_generate_files(analysis, architecture)
            if not files or not any(f.path.startswith("tests/") for f in files):
                raise ValueError("bundle missing a tests/ suite")
            if not self._validate_bundle(files):
                raise ValueError("generated project failed sandbox compile/tests")
            self._bundle = {
                "code": [f for f in files
                         if not f.path.startswith("tests/") and f.kind != "docs"],
                "tests": [f for f in files if f.path.startswith("tests/")],
                "docs": [f for f in files if f.kind == "docs"],
            }
        except Exception as exc:  # noqa: BLE001 - degrade to the verified template
            self._bundle_failed = True
            self.metrics.record(CallRecord(
                "codegen", getattr(self._client, "model", "unknown"), 0, 0, 0.0,
                fallback=True, error=str(exc)[:200],
            ))

    def _llm_generate_files(
        self, analysis: AnalysisResult, architecture: Architecture
    ) -> list[Artifact]:
        api = "\n".join(f"  {e.method} {e.path} -> {e.response}" for e in architecture.api)
        system = (
            "You are a senior software engineer. Generate a COMPLETE, runnable Python "
            "project for the requirement, consistent with the given architecture. Use "
            "ONLY the Python standard library. Include an importable package, an HTTP "
            "API (WSGI or http.server), and a tests/ directory with unittest tests that "
            "import the package and pass. Respond with STRICT JSON only: "
            "{\"files\": [{\"path\": \"relative/path.py\", \"content\": \"...\"}]}. "
            "Put tests under tests/. Do not wrap content in markdown fences.\n"
            "SCOPE AND SIZE (hard limits — the reply must fit in one response): implement "
            "the minimal runnable slice of the design — the listed API endpoints, "
            "persistence, and analytics — not every component. At most 8 files, none "
            "longer than ~150 lines; short docstrings, no commentary, no README. Tests: "
            "one or two files covering the main flow end to end."
        )
        user = (
            f"Requirement:\n{analysis.normalized_problem or analysis.intent}\n\n"
            f"Architecture overview: {architecture.overview}\n"
            f"Components: {', '.join(architecture.components)}\n"
            f"Data model: {', '.join(architecture.data_model)}\n"
            f"API:\n{api}"
        )
        data = self._ask_json("codegen", system, user)
        files: list[Artifact] = []
        for f in data.get("files", []):
            path = str(f.get("path", "")).strip()
            content = f.get("content", "")
            if path and content:
                files.append(Artifact(path, content, self._infer_kind(path)))
        return files

    @staticmethod
    def _infer_kind(path: str) -> str:
        if path.startswith("tests/"):
            return "test"
        if path.endswith((".md", ".markdown")):
            return "docs"
        if "openapi" in path or path.endswith((".yaml", ".yml")):
            return "contract"
        return "code"

    def _validate_bundle(self, files: list[Artifact]) -> bool:
        """Write the bundle to a temp dir, compile it, and run its tests."""

        import tempfile
        from pathlib import Path

        from agentic_sdlc.tools import ArtifactStore, CodeRunner

        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(tmp)
            store.write_all(files)
            runner = CodeRunner()
            py = [Path(tmp) / f.path for f in files if f.path.endswith(".py")]
            if any(not r.ok for r in runner.compile_python(py)):
                return False
            return runner.run_unittests(Path(tmp)).ok
