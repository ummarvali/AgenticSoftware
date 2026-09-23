"""Filesystem tool: safely materialize generated artifacts to disk.

The write path is guarded against directory traversal so a malicious or buggy
artifact path (``../../etc/passwd``) can never escape the run's output directory.
This is a concrete guardrail for "safe and reliable execution".
"""

from __future__ import annotations

from pathlib import Path

from agentic_sdlc.models import Artifact


class ArtifactStore:
    """Writes :class:`Artifact` objects under a fixed, sandboxed root directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, relative: str) -> Path:
        target = (self.root / relative).resolve()
        # Reject anything that resolves outside the sandbox root.
        if self.root not in target.parents and target != self.root:
            raise ValueError(f"unsafe artifact path escapes sandbox: {relative!r}")
        return target

    def write(self, artifact: Artifact) -> Path:
        path = self._safe_path(artifact.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(artifact.content, encoding="utf-8")
        return path

    def write_all(self, artifacts: list[Artifact]) -> list[Path]:
        return [self.write(a) for a in artifacts]
