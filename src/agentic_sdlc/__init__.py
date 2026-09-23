"""Agentic SDLC — an agentic system that turns a requirement into a reviewable
engineering outcome.

The public surface intentionally re-exports the orchestrator and the run helper so
that embedding the engine in another program is a one-liner:

    from agentic_sdlc import run_pipeline
    result = run_pipeline("Build a scalable URL shortener service ...")
"""

from agentic_sdlc.models import (  # noqa: F401
    Requirement,
    RequirementKind,
    AnalysisResult,
    Task,
    TaskGraph,
    Architecture,
    Artifact,
    ValidationReport,
    EngineeringSummary,
    RunResult,
)
from agentic_sdlc.orchestrator.orchestrator import Orchestrator, run_pipeline  # noqa: F401

__all__ = [
    "Requirement",
    "RequirementKind",
    "AnalysisResult",
    "Task",
    "TaskGraph",
    "Architecture",
    "Artifact",
    "ValidationReport",
    "EngineeringSummary",
    "RunResult",
    "Orchestrator",
    "run_pipeline",
]

__version__ = "1.0.0"
