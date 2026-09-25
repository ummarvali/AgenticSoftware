"""Apply an accepted agent run to the working tree (used by the pipeline's acceptance job).

The agent never writes to the repository; this is the separate, human-approved step:

* change mode (the run has CHANGES.diff): write the changed files over ``--repo``,
  keeping each existing file's line endings, so the pull request shows only real edits;
* greenfield: place the generated project under ``generated/<run-id>/``.

    python scripts/apply_change.py runs/<run-id> --repo demo      # brownfield
    python scripts/apply_change.py runs/<run-id>                  # greenfield
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

_RECORDS = {"CHANGES.diff", "ENGINEERING_SUMMARY.md"}


def _inside(root: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", help="run folder (runs/<run-id>)")
    ap.add_argument("--repo", help="brownfield: the folder the change set targets")
    args = ap.parse_args()

    run = Path(args.run)
    arts = run / "artifacts"
    # Only the files the agent produced (listed in its run record) are applied — never files
    # that code executed during validation may have created in the artifacts folder.
    record = json.loads((run / "result.json").read_text(encoding="utf-8"))
    listed = {a["path"] for a in record.get("artifacts", [])}
    files = [arts / p for p in sorted(listed) if (arts / p).is_file() and _inside(arts, arts / p)]
    if (arts / "CHANGES.diff").exists():
        if not args.repo:
            print("this run is a change set: pass --repo <folder it targets>")
            return 2
        root = Path(args.repo)
        applied = []
        for src in files:
            if src.name in _RECORDS and src.parent == arts:
                continue
            rel = src.relative_to(arts)
            dst = root / rel
            if not _inside(root, dst):
                raise SystemExit(f"refusing to write outside {root}: {rel}")
            text = src.read_text(encoding="utf-8").replace("\r\n", "\n")
            if dst.exists() and b"\r\n" in dst.read_bytes():
                text = text.replace("\n", "\r\n")          # keep the file's line endings
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(text.encode("utf-8"))
            applied.append(rel.as_posix())
        print(f"applied {len(applied)} file(s) to {root}: {', '.join(applied)}")
    else:
        dest = Path("generated") / run.name
        if dest.exists():
            shutil.rmtree(dest)
        for src in files:
            target = dest / src.relative_to(arts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
        print(f"placed the generated project ({len(files)} files) under {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
