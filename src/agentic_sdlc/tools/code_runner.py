"""Code-execution tool: compile generated code and run its test suite.

Used by the validator agent to *actually verify* the artifacts it produced rather
than trusting them. Tests run in a subprocess with a timeout so a hang or a runaway
generated test can never block the pipeline.
"""

from __future__ import annotations

import py_compile
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


class CodeRunner:
    """Thin, safe wrapper over ``py_compile`` and ``unittest``."""

    def compile_python(self, paths: list[Path]) -> list[CompileResult]:
        results: list[CompileResult] = []
        for path in paths:
            if path.suffix != ".py":
                continue
            try:
                py_compile.compile(str(path), doraise=True)
                results.append(CompileResult(str(path), True))
            except py_compile.PyCompileError as exc:
                results.append(CompileResult(str(path), False, str(exc)))
        return results

    def run_unittests(
        self, workdir: Path, tests_dir: str = "tests", timeout: float = 120.0
    ) -> TestResult:
        if not (workdir / tests_dir).exists():
            return TestResult(True, "no tests directory; skipped", 0)
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", tests_dir, "-v"],
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return TestResult(False, "test run exceeded timeout", -1)
        # unittest writes its summary (including OK / FAILED) to stderr.
        output = (proc.stdout or "") + (proc.stderr or "")
        return TestResult(proc.returncode == 0, output, proc.returncode)
