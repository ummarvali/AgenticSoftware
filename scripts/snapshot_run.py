"""Copy the most recent run (or a given run id) into examples/<name>/ as a committed record.

Use it to check in the evidence of a live-model run without committing any key:

    python scripts/snapshot_run.py --name llm-run          # latest run under runs/
    python scripts/snapshot_run.py --name llm-run --run 20260924-101500-123

The copy keeps result.json (metrics, events, decisions) and the generated artifacts,
and drops caches. It also scrubs anything that looks like an API key, defensively.
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

_SECRET = re.compile(r"(sk-ant-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,})")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--name", default="llm-run", help="folder name under examples/")
    p.add_argument("--run", help="run id under runs/ (default: the latest)")
    p.add_argument("--runs-root", default="runs")
    args = p.parse_args()

    root = Path(args.runs_root)
    if args.run:
        src = root / args.run
    else:
        candidates = sorted(d for d in root.iterdir() if d.is_dir()) if root.exists() else []
        if not candidates:
            print(f"no runs found under {root}/ — run the agent first")
            return 1
        src = candidates[-1]

    dst = Path("examples") / args.name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.db"))

    scrubbed = 0
    for f in dst.rglob("*"):
        if f.is_file() and f.suffix in {".json", ".md", ".py", ".yaml", ".yml", ".txt"}:
            text = f.read_text(encoding="utf-8", errors="ignore")
            new = _SECRET.sub("<redacted>", text)
            if new != text:
                f.write_text(new, encoding="utf-8")
                scrubbed += 1
    print(f"snapshot: {src} -> {dst} ({scrubbed} file(s) scrubbed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
