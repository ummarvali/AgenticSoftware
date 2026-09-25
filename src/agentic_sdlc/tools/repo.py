"""Existing-repository tool for brownfield work — read-only by construction.

A brownfield change is proposed, never applied: this module reads a repository into
memory, ranks its files by relevance to the requested change, lays a proposed change
set over a *throwaway copy* of it (so the repository's own tests can run against the
change), and renders the change as a unified diff for human review. Nothing here writes
to the repository.
"""

from __future__ import annotations

import difflib
import os
import re
import shutil
from pathlib import Path

from agentic_sdlc.models import Artifact

SOURCE_SUFFIXES = (".py", ".md", ".yaml", ".yml", ".toml", ".txt", ".cfg", ".ini", ".json",
                   ".ts", ".js", ".go", ".java")
SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", "runs", "dist",
             "build", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea", ".vscode"}
MAX_FILE_BYTES = 200_000
MAX_FILES = 400
_STOPWORDS = {"that", "this", "with", "from", "into", "have", "should", "would", "existing",
              "service", "system", "prevent", "make", "build", "when", "where", "instead",
              "the", "and", "for", "add"}


def snapshot(repo: str | Path) -> dict[str, str]:
    """Text source files of the repository, keyed by POSIX relative path (bounded)."""

    root = Path(repo)
    out: dict[str, str] = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for fname in sorted(files):
            path = Path(dirpath) / fname
            if fname in ("ENGINEERING_SUMMARY.md", "CHANGES.diff"):
                continue   # generated records, not source
            if path.suffix not in SOURCE_SUFFIXES and fname not in ("Dockerfile", "Makefile"):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            out[path.relative_to(root).as_posix()] = text.replace("\r\n", "\n")
            if len(out) >= MAX_FILES:
                return out
    return out


def rank(files: dict[str, str], requirement: str) -> list[tuple[int, str]]:
    """(score, path) by term overlap between the requirement and each file's path+content.
    A heuristic for candidate touch points — not semantic search."""

    low = requirement.lower()
    terms = {w for w in re.findall(r"[a-z][a-z0-9_]{3,}", low) if w not in _STOPWORDS}
    if "rate" in low:
        terms |= {"rate", "limit", "throttle", "429", "request", "route", "handler"}
    scored = []
    for path, text in files.items():
        blob = (path + "\n" + text[:65536]).lower()
        score = sum(blob.count(t) for t in terms)
        if score:
            scored.append((score, path))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored


def context_files(files: dict[str, str], requirement: str, budget_chars: int = 60_000) -> dict[str, str]:
    """The files a model needs to make the change: the most relevant sources, then the
    tests and contract that the change must keep passing, within a size budget."""

    ranked = [p for _, p in rank(files, requirement)]
    must = [p for p in files if p.startswith("tests/") or p.endswith(("openapi.yaml", "README.md"))]
    chosen: dict[str, str] = {}
    used = 0
    for p in ranked + [p for p in files if p.endswith(".py")] + must:
        if p in chosen or p not in files:
            continue
        if used + len(files[p]) > budget_chars:
            continue
        chosen[p] = files[p]
        used += len(files[p])
    return chosen


def only_changes(files: dict[str, str], proposed: list[Artifact]) -> list[Artifact]:
    """Drop proposed files identical to what the repository already has."""

    return [a for a in proposed if files.get(a.path) != a.content.replace("\r\n", "\n")]


def materialize_overlay(repo: str | Path, changes: list[Artifact], dest: str | Path) -> Path:
    """Copy the repository to ``dest`` (skipping VCS/venv/cache dirs) and write the change
    set over it. ``dest`` is a throwaway directory; the repository is only read."""

    from agentic_sdlc.tools.filesystem import ArtifactStore

    dest = Path(dest)
    shutil.copytree(repo, dest, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(*SKIP_DIRS, "*.pyc", "*.db"))
    ArtifactStore(dest).write_all(changes)       # path-traversal guard applies
    return dest


def unified_diff(files: dict[str, str], changes: list[Artifact]) -> str:
    """One reviewable patch for the whole change set (new files diff against /dev/null)."""

    parts: list[str] = []
    for a in sorted(changes, key=lambda x: x.path):
        old = files.get(a.path)
        parts.extend(difflib.unified_diff(
            (old or "").splitlines(keepends=True),
            a.content.replace("\r\n", "\n").splitlines(keepends=True),
            fromfile=f"a/{a.path}" if old is not None else "/dev/null",
            tofile=f"b/{a.path}",
        ))
    text = "".join(parts)
    return text if text.endswith("\n") or not text else text + "\n"


def diff_stats(files: dict[str, str], changes: list[Artifact]) -> list[tuple[str, str, int, int]]:
    """(path, 'new'|'modified', lines added, lines removed) per changed file."""

    out = []
    for a in sorted(changes, key=lambda x: x.path):
        old = files.get(a.path)
        added = removed = 0
        for line in difflib.unified_diff((old or "").splitlines(), a.content.splitlines(), lineterm=""):
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
        out.append((a.path, "new" if old is None else "modified", added, removed))
    return out
