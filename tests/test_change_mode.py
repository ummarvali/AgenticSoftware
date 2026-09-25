"""Brownfield change mode: a change set is proposed against an existing repository,
validated on a throwaway copy with the change applied, and the repository is never
modified."""

import json
import tempfile
import unittest
from pathlib import Path

from agentic_sdlc.llm.client import LLMResponse, MetricsCollector
from agentic_sdlc.llm.llm_provider import LLMProvider
from agentic_sdlc.models import Requirement
from agentic_sdlc.orchestrator import Orchestrator, OrchestratorConfig

ROOT = Path(__file__).resolve().parents[1]
RATE_LIMIT = "Add rate limiting to the existing URL shortener API to prevent abuse."


class FakeClient:
    model = "fake-model"

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.users = []

    def complete(self, system, user, *, json_mode=True, timeout=30.0, max_tokens=4096):
        self.calls += 1
        self.users.append(user)
        return LLMResponse(text=self._responses.pop(0), prompt_tokens=10, completion_tokens=10,
                           model=self.model)


def _tiny_repo(tmp: str) -> Path:
    repo = Path(tmp) / "repo"
    (repo / "calc").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "calc" / "__init__.py").write_text("")
    (repo / "calc" / "ops.py").write_text("def add(a, b):\n    return a - b   # bug\n")
    (repo / "tests" / "test_ops.py").write_text(
        "import unittest\nfrom calc.ops import add\n\n"
        "class T(unittest.TestCase):\n    def test_exists(self):\n        self.assertTrue(callable(add))\n")
    return repo


_ADD_TEST = ("<<<FILE tests/test_add.py>>>\nimport unittest\nfrom calc.ops import add\n\n"
             "class T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
             "<<<END FILE>>>\n")
# Code with quotes, backslashes and braces travels verbatim — nothing to escape.
_FIX = ("<<<SUMMARY>>>\nfix add\n<<<END SUMMARY>>>\n"
        "<<<FILE calc/ops.py>>>\ndef add(a, b):\n    \"\"\"Return a + b (was a - b: the bug).\"\"\"\n"
        "    return a + b\n\nFMT = '{\"a\": %d}\\n'\n<<<END FILE>>>\n" + _ADD_TEST)
_TEST_ONLY = "<<<SUMMARY>>>\ntest only\n<<<END SUMMARY>>>\n" + _ADD_TEST


class LLMChangeTests(unittest.TestCase):
    def _provider(self, responses):
        return LLMProvider(FakeClient(responses), max_retries=1, backoff_s=0.0, metrics=MetricsCollector())

    def _args(self, provider, repo):
        from agentic_sdlc.tools import repo as repo_tool
        a = provider._fallback.analyze_requirement(Requirement("Fix the add bug in the existing calc module."))
        return a, provider._fallback.design(a), "Fix the add bug.", repo_tool.snapshot(repo), str(repo)

    def test_valid_change_is_accepted_and_repo_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _tiny_repo(tmp)
            before = (repo / "calc" / "ops.py").read_text()
            p = self._provider([_FIX])
            files, summary = p.generate_change(*self._args(p, repo))
            self.assertEqual(sorted(f.path for f in files), ["calc/ops.py", "tests/test_add.py"])
            self.assertEqual(summary, "fix add")
            self.assertIn('FMT = \'{"a": %d}\\n\'', next(f.content for f in files if f.path == "calc/ops.py"))
            self.assertEqual(p.metrics.fallbacks, 0)
            self.assertEqual((repo / "calc" / "ops.py").read_text(), before)   # read-only
            self.assertIn("calc/ops.py", p._client.users[0])                  # model saw the code

    def test_change_failing_the_repo_tests_gets_one_repair_pass(self):
        # First attempt only adds a test that exposes the bug -> sandbox rejects it;
        # the repair pass (given the failure) fixes the code as well.
        with tempfile.TemporaryDirectory() as tmp:
            repo = _tiny_repo(tmp)
            p = self._provider([_TEST_ONLY, _FIX])
            files, _ = p.generate_change(*self._args(p, repo))
            self.assertIn("calc/ops.py", [f.path for f in files])
            self.assertEqual(p.metrics.fallbacks, 0)
            self.assertEqual(p.metrics.retries, 1)
            self.assertIn("SANDBOX OUTPUT", p._client.users[1])

    def test_change_that_never_passes_falls_back_to_no_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _tiny_repo(tmp)
            p = self._provider([_TEST_ONLY, _TEST_ONLY])
            files, _ = p.generate_change(*self._args(p, repo))
            self.assertEqual(files, [])                                   # offline engine: unknown change
            self.assertEqual(p.metrics.fallbacks, 1)


class OrchestratedChangeTests(unittest.TestCase):
    def test_offline_rate_limit_change_against_demo(self):
        demo = ROOT / "demo"
        before = {p: p.read_bytes() for p in demo.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
        with tempfile.TemporaryDirectory() as tmp:
            r = Orchestrator(OrchestratorConfig(provider="deterministic", output_root=tmp, verbose=False)).run(
                Requirement(RATE_LIMIT, repo_path=str(demo)))
            paths = sorted(a.path for a in r.artifacts)
            self.assertIn("url_shortener/ratelimit.py", paths)
            self.assertIn("CHANGES.diff", paths)
            self.assertNotIn("url_shortener/store.py", paths)             # untouched files are not re-emitted
            self.assertTrue(r.validation.passed)
            tests = next(c for c in r.validation.checks if c.name == "tests pass")
            self.assertIn("Ran 22 tests", tests.detail)                  # 20 existing + 2 new, on the overlay
        after = {p: p.read_bytes() for p in demo.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
        self.assertEqual(before, after)                                  # the repository was never written

    def test_offline_unknown_change_is_reported_not_faked(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Orchestrator(OrchestratorConfig(provider="deterministic", output_root=tmp, verbose=False)).run(
                Requirement("Fix the bug where the stats endpoint returns 200 for unknown codes.",
                            repo_path=str(ROOT / "demo")))
            check = next(c for c in r.validation.checks if c.name == "change set present")
            self.assertFalse(check.passed)
            self.assertTrue(any(e["kind"] == "halted" for e in r.events))  # auto mode never accepts it


if __name__ == "__main__":
    unittest.main()
