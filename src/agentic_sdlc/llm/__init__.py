"""Reasoning providers — the swappable brain behind the agents."""

from agentic_sdlc.llm.base import ReasoningProvider
from agentic_sdlc.llm.deterministic import DeterministicProvider


def get_provider(name: str = "deterministic", **kwargs) -> ReasoningProvider:
    """Factory used by the CLI/orchestrator to pick a backend by name.

    The live backend is imported lazily so the default path never touches the
    optional ``openai`` dependency.
    """

    if name in ("deterministic", "offline", "default"):
        return DeterministicProvider()
    if name in ("openai", "llm"):
        from agentic_sdlc.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(**kwargs)
    raise ValueError(f"unknown provider: {name!r}")


__all__ = ["ReasoningProvider", "DeterministicProvider", "get_provider"]
