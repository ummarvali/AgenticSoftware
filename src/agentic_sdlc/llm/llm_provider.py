"""LLM-first reasoning provider with a deterministic reliability fallback.

Every stage is model-driven: requirement analysis, task decomposition, architecture
design, and **generation** — the model authors the whole project (code + tests) from
the requirement. Each call is guarded by:

* a per-call **timeout** and bounded **retries with backoff** (truncation is not retried
  at the same ceiling),
* lenient JSON parsing with **schema/shape validation** of the model's output,
* for generation, a **sandbox gate** — static safety scan, compile, run the model's own
  tests — with **one repair pass** that feeds the real failure back to the model, and
* a **per-stage fallback** to :class:`DeterministicProvider` if the model errors or
  returns something invalid — so a run always completes, and the record says so.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable

from agentic_sdlc.llm.base import ReasoningProvider
from agentic_sdlc.llm.client import CallRecord, LLMClient, MetricsCollector, TruncatedOutput
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

# ---------------------------------------------------------------------------
# Prompts are version-controlled *context*, not code. Each stage's system prompt
# lives in src/agentic_sdlc/prompts/<stage>.md so it can be reviewed, diffed and
# changed without touching Python. AGENTIC_PROMPTS_DIR overrides the directory.
# ---------------------------------------------------------------------------
_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


def load_prompt(stage: str) -> str:
    override = os.environ.get("AGENTIC_PROMPTS_DIR")
    base = Path(override) if override else _PROMPTS_DIR
    path = base / f"{stage}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt for stage '{stage}' not found: {path}")
    return path.read_text(encoding="utf-8").strip()


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


# Output CEILINGS per stage — not budgets. A ceiling costs nothing (you pay for tokens the
# model generates, not for the room it is allowed), so they are set at the model's real
# capacity. Models that reason before answering consume part of this room, and a legitimate
# run must never be cut off by it. On the Anthropic client anything above 8192 streams; a
# model that rejects a ceiling as too large is retried with a smaller one
# (client._call_with_ceiling).
_MAX_TOKENS = {"analyze": 16000, "decompose": 32000, "design": 32000, "codegen": 64000}
# Per-request timeouts (the SDK applies them to connect/read), generous for the same reason.
# Streaming keeps the connection alive, so these only catch a genuinely hung request.
_TIMEOUTS = {"analyze": 300.0, "decompose": 600.0, "design": 600.0, "codegen": 1800.0}


def _analysis_context(analysis: AnalysisResult) -> str:
    """The same context every downstream stage sees, so the design honours the analyst's
    assumptions and the code honours the design — stages are stateless calls, so nothing
    is shared unless it is passed explicitly."""

    frs = "\n".join(f"  - {f}" for f in analysis.functional_requirements) or "  - (none stated)"
    nfrs = "\n".join(f"  - {n}" for n in analysis.non_functional_requirements) or "  - (none stated)"
    assumptions = "\n".join(
        f"  - {a.question} -> ASSUME: {a.default_assumption}" for a in analysis.ambiguities
    ) or "  - (none)"
    return (
        f"Problem:\n{analysis.normalized_problem or analysis.intent}\n\n"
        f"Functional requirements:\n{frs}\n\n"
        f"Non-functional requirements:\n{nfrs}\n\n"
        f"Default assumptions already agreed (honour them; do not re-open them):\n{assumptions}\n"
    )


def _coerce_json(text: str) -> dict:
    """Parse JSON from a model reply, tolerating an outer code fence, surrounding prose,
    and raw control characters inside strings (``strict=False``: models emitting source
    code — Go, Makefiles, YAML — often leave literal tabs/newlines unescaped).

    Only the *outer* fence is stripped: generated files (a README, a Go file) routinely
    contain their own fenced blocks, so splitting on every fence would corrupt them."""

    s = text.strip()
    try:
        return json.loads(s, strict=False)
    except Exception:
        pass
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
        try:
            return json.loads(s, strict=False)
        except Exception:
            pass
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        return json.loads(s[start:end + 1], strict=False)
    raise ValueError("no JSON object found")


def _enforce_plan_invariants(tasks: list[Task]) -> None:
    """Make a model-authored plan safe to execute, or reject it.

    Required: at least one code, tests, validate and summary task. Then every validate
    task is made to depend on every design/impact/code/tests/docs task that is not
    already downstream of it (so validation sees the work it reports on), and every
    summary task on every validate task. Edges that would create a cycle are skipped,
    so a plan the model deliberately sequenced (e.g. a load test after validation) is
    kept as the model wrote it."""

    missing = {"code", "tests", "validate", "summary"} - {t.category for t in tasks}
    if missing:
        raise ValueError(f"LLM plan lacks {', '.join(sorted(missing))} task(s)")
    by_id = {t.id: t for t in tasks}

    def ancestors(tid: str) -> set[str]:
        seen: set[str] = set()
        stack = list(by_id[tid].depends_on) if tid in by_id else []
        while stack:
            d = stack.pop()
            if d in seen or d not in by_id:
                continue
            seen.add(d)
            stack.extend(by_id[d].depends_on)
        return seen

    def link(downstream: list[Task], upstream_categories: set[str]) -> None:
        for d in downstream:
            for u in tasks:
                if (u.category in upstream_categories and u.id != d.id
                        and u.id not in d.depends_on and d.id not in ancestors(u.id)):
                    d.depends_on.append(u.id)

    link([t for t in tasks if t.category == "validate"],
         {"design", "codebase_impact", "code", "tests", "docs"})
    link([t for t in tasks if t.category == "summary"], {"validate"})


class BudgetExceeded(RuntimeError):
    """Raised before a model call when the run's call/cost budget is spent."""


class LLMProvider(ReasoningProvider):
    """Model-driven reasoning; deterministic fallback for reliability."""

    name = "llm"

    def __init__(
        self,
        client: LLMClient,
        *,
        timeout: float = 300.0,
        max_retries: int = 3,
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
        # Circuit breaker, not a budget: a legitimate run makes 4-8 calls and costs cents,
        # so these defaults sit far above normal and trip only on something abnormal — a
        # runaway plan, a retry storm, or a hostile prompt engineered to burn tokens. Once
        # tripped, remaining stages degrade to the deterministic engine and the record
        # says so. Tune per environment with the env vars.
        self._max_calls = max_calls if max_calls is not None else int(
            os.environ.get("AGENTIC_LLM_MAX_CALLS", "200"))
        self._max_cost_usd = max_cost_usd if max_cost_usd is not None else float(
            os.environ.get("AGENTIC_LLM_MAX_COST_USD", "10.00"))
        # code/docs generators may run concurrently in one DAG level; the model must
        # author the project exactly once, so the bundle is built under a lock.
        self._bundle_lock = threading.Lock()

    # -- LLM call with retries + metrics ---------------------------------- #

    def _budget_check(self, stage: str) -> None:
        calls = len(self.metrics.calls)
        cost = self.metrics.breaker_cost_usd
        if calls >= self._max_calls:
            raise BudgetExceeded(f"{stage}: LLM call budget exhausted ({calls}/{self._max_calls})")
        if cost >= self._max_cost_usd:
            raise BudgetExceeded(f"{stage}: LLM cost budget exhausted (~${cost:.4f} >= ${self._max_cost_usd:.2f})")

    def _ask_json(self, stage: str, system: str, user: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            self._budget_check(stage)          # every attempt, not just the first
            start = time.time()
            resp = None
            try:
                resp = self._client.complete(system, user, json_mode=True,
                                             timeout=_TIMEOUTS.get(stage, self._timeout),
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
                # Failed attempts still cost money: count their tokens when known, so the
                # cost estimate and the circuit breaker see them.
                src = resp if resp is not None else exc
                self.metrics.record(CallRecord(
                    stage, getattr(self._client, "model", "unknown"),
                    getattr(src, "prompt_tokens", 0) or 0,
                    getattr(src, "completion_tokens", 0) or 0,
                    round(time.time() - start, 3), error=str(exc)[:200],
                ))
                if isinstance(exc, TruncatedOutput):
                    break                      # same ceiling would truncate again
                if attempt < self._max_retries and self._backoff:
                    time.sleep(self._backoff * attempt)
        raise RuntimeError(f"LLM stage '{stage}' failed: {last_error}")

    def _with_fallback(self, stage: str, llm_fn: Callable[[], Any],
                       fallback_fn: Callable[[], Any]) -> Any:
        try:
            return llm_fn()
        except Exception as exc:  # noqa: BLE001 - degrade to deterministic
            self.metrics.record(CallRecord(
                stage, getattr(self._client, "model", "unknown"), 0, 0, 0.0,
                fallback=True, error=str(exc)[:200], event=True,
            ))
            return fallback_fn()

    # -- ReasoningProvider API -------------------------------------------- #

    def analyze_requirement(self, requirement: Requirement) -> AnalysisResult:
        # Analysis starts a run: never carry a previous run's generated bundle over.
        with self._bundle_lock:
            self._bundle, self._bundle_failed = None, False

        def llm() -> AnalysisResult:
            system = load_prompt("analyze")
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
            system = load_prompt("decompose")
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
            # Validation guardrail: reject unknown categories, a plan missing a required
            # stage, dangling dependencies or a cycle; make validation/summary depend on
            # the work they report on.
            if not tasks or any(t.category not in _KNOWN_CATEGORIES for t in tasks):
                raise ValueError("LLM plan has invalid categories")
            _enforce_plan_invariants(tasks)
            graph = TaskGraph(tasks=tasks)
            graph.validate_acyclic()
            return graph

        return self._with_fallback(
            "decompose", llm, lambda: self._fallback.decompose(analysis)
        )

    def design(self, analysis: AnalysisResult) -> Architecture:
        def llm() -> Architecture:
            system = load_prompt("design")
            data = self._ask_json("design", system, _analysis_context(analysis))
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
            ok, output = self._validate_bundle(files)
            if not ok:
                # Bounded feedback loop (one pass): the sandbox result goes back to the
                # model as a repair brief. Recorded as a retry, not a fallback.
                self.metrics.record(CallRecord(
                    "codegen", getattr(self._client, "model", "unknown"), 0, 0, 0.0,
                    error="sandbox rejected bundle; repair pass: " + output[-160:].replace("\n", " "),
                    event=True,
                ))
                files = self._llm_repair_files(analysis, architecture, files, output)
                if not files or not any(f.path.startswith("tests/") for f in files):
                    raise ValueError("repaired bundle missing a tests/ suite")
                ok, output = self._validate_bundle(files)
                if not ok:
                    raise ValueError("generated project failed sandbox compile/tests after one repair pass")
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
                fallback=True, error=str(exc)[:200], event=True,
            ))

    def _llm_generate_files(
        self, analysis: AnalysisResult, architecture: Architecture
    ) -> list[Artifact]:
        api = "\n".join(f"  {e.method} {e.path} -> {e.response}" for e in architecture.api)
        system = load_prompt("codegen")
        user = (
            _analysis_context(analysis) + "\n"
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
            if path and (content or path.endswith("__init__.py")):
                files.append(Artifact(path, content or "", self._infer_kind(path)))
        return files

    def _llm_repair_files(
        self, analysis: AnalysisResult, architecture: Architecture,
        files: list[Artifact], sandbox_output: str,
    ) -> list[Artifact]:
        """One repair pass: previous bundle + sandbox result -> corrected bundle."""
        api = "\n".join(f"  {e.method} {e.path} -> {e.response}" for e in architecture.api)
        listing = "\n\n".join(f"### {f.path}\n{f.content}" for f in files)
        user = (
            _analysis_context(analysis) + "\n"
            f"Architecture overview: {architecture.overview}\n"
            f"API:\n{api}\n\n"
            f"PREVIOUS BUNDLE:\n{listing}\n\n"
            f"SANDBOX OUTPUT (compile errors / unittest result):\n{sandbox_output[-4000:]}"
        )
        data = self._ask_json("codegen", load_prompt("codegen_repair"), user)
        repaired: list[Artifact] = []
        for f in data.get("files", []):
            path = str(f.get("path", "")).strip()
            content = f.get("content", "")
            if path and (content or path.endswith("__init__.py")):
                repaired.append(Artifact(path, content or "", self._infer_kind(path)))
        return repaired

    @staticmethod
    def _infer_kind(path: str) -> str:
        if path.startswith("tests/"):
            return "test"
        if path.endswith((".md", ".markdown")):
            return "docs"
        if "openapi" in path or path.endswith((".yaml", ".yml")):
            return "contract"
        return "code"

    def _validate_bundle(self, files: list[Artifact]) -> tuple[bool, str]:
        """Write the bundle to a temp dir, scan it, compile it, and run its tests.

        Nothing is executed if the static safety scan finds a high-severity issue.
        Returns (ok, output) so a rejection can be fed back to the model."""

        import tempfile

        from agentic_sdlc.tools import ArtifactStore, CodeRunner
        from agentic_sdlc.tools.static_check import scan_tree

        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(tmp)
            store.write_all(files)
            high = [f for f in scan_tree(Path(tmp)) if f.severity == "high"]
            if high:
                return False, "static safety scan (code not executed):\n" + "\n".join(
                    str(f) for f in high[:10])
            runner = CodeRunner()
            py = [Path(tmp) / f.path for f in files if f.path.endswith(".py")]
            failed = [r for r in runner.compile_python(py) if not r.ok]
            if failed:
                return False, "\n".join(f"{r.path}: {r.error}" for r in failed)
            result = runner.run_unittests(Path(tmp))
            return result.ok, result.output
