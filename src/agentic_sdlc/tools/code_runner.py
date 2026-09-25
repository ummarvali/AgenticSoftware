"""Code-execution tool: compile generated code and run its test suite.

Used by the validator agent to *actually verify* the artifacts it produced rather
than trusting them. Tests run in a subprocess with a timeout so a hang or a runaway
generated test can never block the pipeline.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CompileResult:
    path: str
    ok: bool
    error: str = ""


@dataclass
class TestResult:
    ok: bool
    output: str
    returncode: int


_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def scrubbed_env() -> dict[str, str]:
    """Environment for executing *generated* code: every variable that looks like a
    credential (API keys, tokens, passwords) is removed, so model-authored tests can
    not read the key that produced them from their own environment. (The sandbox is
    process-level: on Linux a same-user process can still read its parent's
    environment via /proc — production runs generated code in a separate container or
    user; see README §8.) Byte-code writing is disabled so the artifact folder stays
    clean."""

    env = {k: v for k, v in os.environ.items()
           if not any(m in k.upper() for m in _SECRET_MARKERS)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


_UNITTEST_BOOTSTRAP = (
    "import sys, unittest; root, tests = sys.argv[1], sys.argv[2]; "
    "sys.path.insert(0, root); "
    "unittest.main(module=None, argv=['unittest', 'discover', '-s', tests, '-v'])"
)


class CodeRunner:
    """Thin, safe wrapper over in-memory compilation and an isolated ``unittest`` run."""

    def compile_python(self, paths: list[Path]) -> list[CompileResult]:
        results: list[CompileResult] = []
        for path in paths:
            if path.suffix != ".py":
                continue
            # Compiled in memory: proves syntax without writing __pycache__ into the
            # artifact folder a human will review.
            try:
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
                results.append(CompileResult(str(path), True))
            except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
                results.append(CompileResult(str(path), False, f"{type(exc).__name__}: {exc}"))
        return results

    def run_unittests(
        self, workdir: Path, tests_dir: str = "tests", timeout: float = 120.0
    ) -> TestResult:
        if not (workdir / tests_dir).exists():
            return TestResult(True, "no tests directory; skipped", 0)
        try:
            # -I (isolated): no user site-packages, PYTHON* variables ignored, and the
            # working directory is NOT on sys.path while the real stdlib ``unittest`` is
            # imported — so a generated ``unittest.py`` cannot fake a passing run.
            # -B: no byte-code files in the artifact folder.
            proc = subprocess.run(
                [sys.executable, "-I", "-B", "-c", _UNITTEST_BOOTSTRAP,
                 str(workdir.resolve()), tests_dir],
                cwd=str(workdir),
                env=scrubbed_env(),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return TestResult(False, "test run exceeded timeout", -1)
        # unittest writes its summary (including OK / FAILED) to stderr.
        output = (proc.stdout or "") + (proc.stderr or "")
        return TestResult(proc.returncode == 0, output, proc.returncode)
