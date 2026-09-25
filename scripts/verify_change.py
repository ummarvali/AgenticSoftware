"""Re-verify a recorded brownfield change set against the repository it targets.

A change-mode run records only the files it adds or changes (plus CHANGES.diff), so its
artifacts cannot be tested on their own. This script does what the Validator did: copy
the repository to a throwaway directory, lay the change set over it, and run the
repository's test suite (existing tests + the new ones). The repository is not modified.

    python scripts/verify_change.py examples/llm-run-brownfield --repo demo
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentic_sdlc.models import Artifact  # noqa: E402
from agentic_sdlc.tools import CodeRunner  # noqa: E402
from agentic_sdlc.tools import repo as repo_tool  # noqa: E402

_NOT_CHANGES = {"CHANGES.diff", "ENGINEERING_SUMMARY.md"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", help="recorded run folder (contains artifacts/CHANGES.diff)")
    ap.add_argument("--repo", required=True, help="the repository the change targets")
    args = ap.parse_args()

    arts = Path(args.run) / "artifacts"
    if not (arts / "CHANGES.diff").exists():
        print(f"{arts} has no CHANGES.diff — not a change-mode run")
        return 2
    changes = [Artifact(p.relative_to(arts).as_posix(), p.read_text(encoding="utf-8"))
               for p in sorted(arts.rglob("*")) if p.is_file()
               and p.name not in _NOT_CHANGES and "__pycache__" not in p.parts]
    print(f"change set ({len(changes)} files): {', '.join(a.path for a in changes)}")
    with tempfile.TemporaryDirectory() as tmp:
        overlay = repo_tool.materialize_overlay(args.repo, changes, Path(tmp) / "overlay")
        result = CodeRunner().run_unittests(overlay)
    tail = [l for l in result.output.strip().splitlines() if l.startswith(("Ran ", "OK", "FAILED"))]
    print(f"{args.repo} + change set: {' | '.join(tail) or result.output[-400:]}")
    print("PASS" if result.ok else "FAIL")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
