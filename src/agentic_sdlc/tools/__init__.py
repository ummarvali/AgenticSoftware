"""Tool box — the concrete capabilities agents are allowed to use."""

from dataclasses import dataclass

from agentic_sdlc.tools.code_runner import CodeRunner
from agentic_sdlc.tools.filesystem import ArtifactStore


@dataclass
class ToolBox:
    """Bundle of side-effecting tools handed to agents through their context.

    Passing tools explicitly (instead of letting agents import globals) keeps the
    blast radius of each agent visible and testable.
    """

    artifacts: ArtifactStore
    runner: CodeRunner


__all__ = ["ToolBox", "ArtifactStore", "CodeRunner"]
