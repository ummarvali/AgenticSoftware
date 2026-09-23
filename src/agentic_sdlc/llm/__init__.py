"""Reasoning providers — the swappable brain behind the agents."""

from agentic_sdlc.llm.base import ReasoningProvider
from agentic_sdlc.llm.client import detect_backend, llm_configured
from agentic_sdlc.llm.deterministic import DeterministicProvider


def get_provider(name: str = "auto", **kwargs) -> ReasoningProvider:
    """Factory used by the CLI/orchestrator to pick a backend by name.

    * ``deterministic`` — offline, reproducible rule engine (no network).
    * ``anthropic`` / ``claude`` — Claude via the Anthropic API.
    * ``openai`` / ``llm`` — OpenAI, Azure OpenAI, or any OpenAI-compatible endpoint.
    * ``auto`` (default) — use whichever LLM the environment configures
      (``ANTHROPIC_API_KEY`` → Claude; ``OPENAI_API_KEY`` / ``AZURE_OPENAI_ENDPOINT`` /
      ``OPENAI_BASE_URL`` → OpenAI-compatible), else deterministic. This makes the system
      a real LLM agent whenever a key exists, while never failing a keyless demo.

    All LLM imports are lazy, so the deterministic path never touches ``openai``/``anthropic``.
    """

    if name in ("deterministic", "offline"):
        return DeterministicProvider()
    if name in ("anthropic", "claude"):
        return _build_llm("anthropic", **kwargs)
    if name in ("llm", "openai"):
        return _build_llm("openai", **kwargs)
    if name in ("auto", "default"):
        backend = detect_backend()
        return _build_llm(backend, **kwargs) if backend else DeterministicProvider()
    raise ValueError(f"unknown provider: {name!r}")


def _build_llm(backend: str, **kwargs) -> ReasoningProvider:
    from agentic_sdlc.llm.client import AnthropicClient, OpenAIClient
    from agentic_sdlc.llm.llm_provider import LLMProvider

    model = kwargs.pop("model", None)
    client = AnthropicClient(model=model) if backend == "anthropic" else OpenAIClient(model=model)
    return LLMProvider(client, **kwargs)


__all__ = ["ReasoningProvider", "DeterministicProvider", "get_provider", "llm_configured"]
