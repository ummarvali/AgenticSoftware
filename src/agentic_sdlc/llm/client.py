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
import time
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


# Approximate USD price per 1M tokens (input, output). Only used for a cost estimate;
# unknown models fall back to a conservative default.
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
}
_DEFAULT_PRICE = (1.00, 3.00)


def _price_for(model: str) -> tuple[float, float]:
    for prefix, price in _PRICES.items():
        if model.startswith(prefix):
            return price
    return _DEFAULT_PRICE


@dataclass
class CallRecord:
    stage: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    fallback: bool = False
    error: str = ""


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
    def est_cost_usd(self) -> float:
        total = 0.0
        for c in self.calls:
            pin, pout = _price_for(c.model)
            total += (c.prompt_tokens / 1_000_000) * pin
            total += (c.completion_tokens / 1_000_000) * pout
        return round(total, 6)

    def summary(self) -> str:
        return (f"{len(self.calls)} LLM calls, {self.total_tokens} tokens, "
                f"~${self.est_cost_usd:.4f}, {self.fallbacks} fallbacks")

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": [asdict(c) for c in self.calls],
            "total_tokens": self.total_tokens,
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
            "temperature": 0.2,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._client.chat.completions.create(**kwargs)
        usage = getattr(resp, "usage", None)
        if getattr(resp.choices[0], "finish_reason", "") == "length":
            raise RuntimeError(f"model output truncated at max_tokens={max_tokens}")
        return LLMResponse(
            text=resp.choices[0].message.content or "",
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            model=self.model,
        )


class AnthropicClient:
    """Native Claude (Anthropic) chat client (lazy-imported).

    Requires an API key from console.anthropic.com in ``ANTHROPIC_API_KEY`` (a
    claude.ai subscription is a separate product and cannot be used here). JSON is
    coerced by prefilling the assistant turn with ``{`` — the standard Anthropic
    technique for reliable structured output.
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
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            timeout=timeout,
        )
        # Concatenate every text block (thinking blocks have no .text and are skipped).
        text = "".join(getattr(b, "text", "") or "" for b in (resp.content or []))
        if getattr(resp, "stop_reason", "") == "max_tokens":
            raise RuntimeError(f"model output truncated at max_tokens={max_tokens} "
                               f"(got {len(text)} chars)")
        if not text.strip():
            raise RuntimeError(f"model returned no text (stop_reason={getattr(resp, 'stop_reason', '?')})")
        usage = getattr(resp, "usage", None)
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
