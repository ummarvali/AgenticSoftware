"""LLM client abstraction + usage metrics.

The provider talks to models through the small :class:`LLMClient` seam so it can be
driven by a real backend in production and a deterministic fake in tests (no network,
no keys). :class:`OpenAIClient` supports **OpenAI**, **Azure OpenAI**, and any
OpenAI-compatible endpoint (Ollama, vLLM, LiteLLM) selected purely by environment.

:class:`MetricsCollector` records per-call tokens, latency, cost, and fallbacks — the
observability an SRE needs to reason about a run's reliability and spend.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field, asdict
from typing import Any, Protocol


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""


class LLMClient(Protocol):
    """Minimal chat interface every backend (real or fake) implements."""

    def complete(self, system: str, user: str, *, json_mode: bool = True,
                 timeout: float = 30.0, max_tokens: int = 4096) -> LLMResponse: ...


# Approximate USD list price per 1M tokens (input, output), matched by longest model-id
# prefix. Only used for the cost estimate and the spend circuit breaker. Unknown models
# are priced at a Sonnet-class rate ($3/$15), which over- rather than under-estimates
# for most models.
_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "o4-mini": (1.10, 4.40),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-7-sonnet": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-opus-4": (15.00, 75.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-opus-4-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
}
_DEFAULT_PRICE = (3.00, 15.00)


def _temperature(default: float | None) -> float | None:
    """Sampling temperature for structured engineering output (plans, designs, code).

    ``AGENTIC_LLM_TEMPERATURE`` overrides; otherwise ``default`` is used. ``None`` means
    "do not send the parameter" — some SDK/model versions reject it, and the model's own
    default is then used. Callers must tolerate a rejected parameter (see ``_call``)."""

    raw = os.environ.get("AGENTIC_LLM_TEMPERATURE")
    if raw is None:
        return default
    return None if raw.strip().lower() in ("", "none", "default") else float(raw)


_CEILING_FALLBACKS = (32000, 16000, 8192, 4096)


def _call_with_ceiling(fn, kwargs: dict):
    """Invoke an SDK method; if the API rejects ``max_tokens`` as above the model's
    maximum, retry with the next smaller ceiling. A ceiling is a safety limit, never a
    reason for a stage to fail."""

    while True:
        try:
            return _call(fn, kwargs)
        except Exception as exc:  # noqa: BLE001 - only max_tokens rejections are handled
            msg = str(exc).lower()
            current = int(kwargs.get("max_tokens", 0) or 0)
            smaller = [c for c in _CEILING_FALLBACKS if c < current]
            if "max_tokens" in msg and smaller and any(w in msg for w in ("maximum", "exceed", "invalid", "too large", "at most")):
                kwargs = {**kwargs, "max_tokens": smaller[0]}
                continue
            raise


_REJECTION_WORDS = ("unsupported", "not supported", "does not support", "unexpected keyword")


def _call(fn, kwargs: dict, *, optional: tuple[str, ...] = ("temperature",)):
    """Invoke an SDK method, adapting to parameters the installed SDK or the selected
    model rejects instead of failing the whole stage over a knob:

    * an optional sampling parameter (``temperature``) rejected by the SDK (TypeError)
      or by the API (HTTP 400 "unsupported ...") is dropped;
    * models that require ``max_completion_tokens`` instead of ``max_tokens`` (OpenAI
      reasoning models) get the parameter renamed."""

    try:
        return fn(**kwargs)
    except Exception as exc:  # noqa: BLE001 - re-raised unless it is a parameter rejection
        msg = str(exc)
        if "max_completion_tokens" in msg and "max_tokens" in kwargs:
            renamed = {k: v for k, v in kwargs.items() if k != "max_tokens"}
            renamed["max_completion_tokens"] = kwargs["max_tokens"]
            return _call(fn, renamed, optional=optional)
        rejected = isinstance(exc, TypeError) or any(w in msg.lower() for w in _REJECTION_WORDS)
        if rejected:
            for name in optional:
                if name in kwargs and name in msg:
                    kwargs = {k: v for k, v in kwargs.items() if k != name}
                    return _call(fn, kwargs, optional=tuple(o for o in optional if o != name))
        raise


def _price_for(model: str) -> tuple[float, float]:
    """Longest matching prefix wins (``gpt-4o-mini`` must not be priced as ``gpt-4o``)."""

    matches = [p for p in _PRICES if model.startswith(p)]
    return _PRICES[max(matches, key=len)] if matches else _DEFAULT_PRICE


class TruncatedOutput(RuntimeError):
    """The model hit the output ceiling. Carries the usage so the spend is counted, and
    is not retried at the same ceiling (it would truncate again)."""

    def __init__(self, message: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        super().__init__(message)
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


@dataclass
class CallRecord:
    stage: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    fallback: bool = False
    error: str = ""
    event: bool = False   # a pipeline event (sandbox rejection, fallback), not an API request


@dataclass
class MetricsCollector:
    """Aggregates per-call LLM usage for observability."""

    calls: list[CallRecord] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def record(self, rec: CallRecord) -> None:
        with self._lock:
            self._record_locked(rec)

    def _record_locked(self, rec: CallRecord) -> None:
        self.calls.append(rec)

    @property
    def total_tokens(self) -> int:
        return sum(c.prompt_tokens + c.completion_tokens for c in self.calls)

    @property
    def fallbacks(self) -> int:
        return sum(1 for c in self.calls if c.fallback)

    @property
    def api_calls(self) -> int:
        return sum(1 for c in self.calls if not c.event)

    @property
    def retries(self) -> int:
        """Failed attempts that were retried or repaired (API errors, bad JSON, sandbox
        rejections) — excludes the terminal fallback records."""
        return sum(1 for c in self.calls if c.error and not c.fallback)

    @property
    def est_cost_usd(self) -> float:
        total = 0.0
        for c in self.calls:
            pin, pout = _price_for(c.model)
            total += (c.prompt_tokens / 1_000_000) * pin
            total += (c.completion_tokens / 1_000_000) * pout
        return round(total, 6)

    def summary(self) -> str:
        return (f"{self.api_calls} LLM calls, {self.total_tokens} tokens, "
                f"~${self.est_cost_usd:.4f}, {self.retries} retries, {self.fallbacks} fallbacks")

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": [asdict(c) for c in self.calls],
            "api_calls": self.api_calls,
            "total_tokens": self.total_tokens,
            "retries": self.retries,
            "fallbacks": self.fallbacks,
            "est_cost_usd": self.est_cost_usd,
        }


class OpenAIClient:
    """OpenAI / Azure OpenAI / OpenAI-compatible chat client (lazy-imported).

    Backend is chosen from the environment:
      * ``AZURE_OPENAI_ENDPOINT`` (+ ``AZURE_OPENAI_API_KEY``) → Azure OpenAI
      * ``OPENAI_BASE_URL`` → any OpenAI-compatible server (Ollama/vLLM/LiteLLM)
      * otherwise → public OpenAI with ``OPENAI_API_KEY``
    """

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        try:
            if azure_endpoint:
                from openai import AzureOpenAI

                self._client = AzureOpenAI(
                    azure_endpoint=azure_endpoint,
                    api_key=os.environ.get("AZURE_OPENAI_API_KEY"),
                    api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-06-01"),
                )
            else:
                from openai import OpenAI

                self._client = OpenAI(
                    api_key=os.environ.get("OPENAI_API_KEY"),
                    base_url=os.environ.get("OPENAI_BASE_URL"),
                )
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "The 'openai' package is required for the LLM backend. "
                "Install it with: pip install -e \".[llm]\""
            ) from exc

    def complete(self, system: str, user: str, *, json_mode: bool = True,
                 timeout: float = 30.0, max_tokens: int = 4096) -> LLMResponse:  # pragma: no cover - network
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "timeout": timeout,
        }
        temp = _temperature(0.2)
        if temp is not None:
            kwargs["temperature"] = temp
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = _call_with_ceiling(self._client.chat.completions.create, kwargs)
        usage = getattr(resp, "usage", None)
        if getattr(resp.choices[0], "finish_reason", "") == "length":
            raise TruncatedOutput(f"model output truncated at max_tokens={max_tokens}",
                                  getattr(usage, "prompt_tokens", 0) or 0,
                                  getattr(usage, "completion_tokens", 0) or 0)
        return LLMResponse(
            text=resp.choices[0].message.content or "",
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            model=self.model,
        )


class AnthropicClient:
    """Native Claude (Anthropic) chat client (lazy-imported).

    Requires an API key from console.anthropic.com in ``ANTHROPIC_API_KEY`` (a
    claude.ai subscription is a separate product and cannot be used here). JSON output
    is requested in the system prompt and parsed leniently by the provider; the model is
    ``ANTHROPIC_MODEL`` if set, otherwise the first Sonnet model the account can list.
    Outputs above 8192 tokens are streamed.
    """

    def __init__(self, model: str | None = None) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "The 'anthropic' package is required for the Claude backend. "
                "Install it with: pip install -e \".[anthropic]\""
            ) from exc
        self._client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        # Honor an explicit model; else auto-pick one this account can access.
        self.model = model or os.environ.get("ANTHROPIC_MODEL") or self._auto_model()

    def _auto_model(self) -> str:
        try:
            ids = [m.id for m in self._client.models.list(limit=50).data]
        except Exception:
            return "claude-3-5-sonnet-20241022"
        for mid in ids:
            if "sonnet" in mid:
                return mid
        return ids[0] if ids else "claude-3-5-sonnet-20241022"

    def complete(self, system: str, user: str, *, json_mode: bool = True,
                 timeout: float = 30.0, max_tokens: int = 4096) -> LLMResponse:  # pragma: no cover - network
        if json_mode:
            system = system + "\n\nReturn ONLY a valid JSON object: no prose, no markdown fences."
        kwargs = dict(model=self.model, max_tokens=max_tokens, system=system,
                      messages=[{"role": "user", "content": user}], timeout=timeout)
        temp = _temperature(None)   # Anthropic: send only when explicitly configured
        if temp is not None:
            kwargs["temperature"] = temp
        if max_tokens > 8192:
            # Long outputs (a whole project) can take several minutes to generate. Streaming
            # keeps the connection alive token by token instead of waiting on one response,
            # which is what the Anthropic SDK requires for long non-interactive generations.
            with _call_with_ceiling(self._client.messages.stream, kwargs) as stream:
                for _ in stream.text_stream:
                    pass
                resp = stream.get_final_message()
        else:
            resp = _call_with_ceiling(self._client.messages.create, kwargs)
        # Concatenate every text block (thinking blocks have no .text and are skipped).
        text = "".join(getattr(b, "text", "") or "" for b in (resp.content or []))
        usage = getattr(resp, "usage", None)
        if getattr(resp, "stop_reason", "") == "max_tokens":
            raise TruncatedOutput(f"model output truncated at max_tokens={max_tokens} "
                                  f"(got {len(text)} chars)",
                                  getattr(usage, "input_tokens", 0) or 0,
                                  getattr(usage, "output_tokens", 0) or 0)
        if not text.strip():
            raise RuntimeError(f"model returned no text (stop_reason={getattr(resp, 'stop_reason', '?')})")
        return LLMResponse(
            text=text,
            prompt_tokens=getattr(usage, "input_tokens", 0) or 0,
            completion_tokens=getattr(usage, "output_tokens", 0) or 0,
            model=self.model,
        )


def detect_backend() -> str | None:
    """Return the LLM backend implied by the environment, or ``None`` if unset."""

    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if (os.environ.get("OPENAI_API_KEY")
            or os.environ.get("AZURE_OPENAI_ENDPOINT")
            or os.environ.get("OPENAI_BASE_URL")):
        return "openai"
    return None


def llm_configured() -> bool:
    """True if any supported LLM backend is configured via the environment."""

    return detect_backend() is not None
