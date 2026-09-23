"""Tests for the LLM provider using a fake client (no network, no API key).

These prove the model-driven path parses structured output correctly, records usage
metrics, and — critically for reliability — falls back to the deterministic engine when
the model errors or returns malformed JSON.
"""

import json
import unittest

from agentic_sdlc.llm.client import LLMResponse, MetricsCollector
from agentic_sdlc.llm.llm_provider import LLMProvider
from agentic_sdlc.models import Requirement, RequirementKind


class FakeClient:
    """Returns canned responses; an item that is an Exception is raised instead."""

    model = "fake-model"

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def complete(self, system, user, *, json_mode=True, timeout=30.0):
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(text=item, prompt_tokens=12, completion_tokens=8, model=self.model)


def _provider(responses):
    metrics = MetricsCollector()
    return LLMProvider(FakeClient(responses), max_retries=2, backoff_s=0.0, metrics=metrics)


class LLMProviderTests(unittest.TestCase):
    def test_analyze_parses_model_json(self):
        payload = json.dumps({
            "kind": "greenfield",
            "intent": "build it",
            "normalized_problem": "problem",
            "functional_requirements": ["fr1"],
            "non_functional_requirements": ["nfr1"],
            "ambiguities": [{"question": "q", "why_it_matters": "w", "default_assumption": "d"}],
            "domain": "url_shortener",
            "confidence": 0.9,
        })
        provider = _provider([payload])
        analysis = provider.analyze_requirement(Requirement("Build a URL shortener."))
        self.assertEqual(analysis.kind, RequirementKind.GREENFIELD)
        self.assertEqual(analysis.domain, "url_shortener")
        self.assertEqual(len(analysis.ambiguities), 1)
        self.assertGreater(provider.metrics.total_tokens, 0)

    def test_decompose_parses_valid_plan(self):
        payload = json.dumps({"tasks": [
            {"id": "design", "title": "Design", "depends_on": [], "category": "design"},
            {"id": "code", "title": "Code", "depends_on": ["design"], "category": "code"},
            {"id": "tests", "title": "Tests", "depends_on": ["code"], "category": "tests"},
            {"id": "docs", "title": "Docs", "depends_on": ["design"], "category": "docs"},
            {"id": "validate", "title": "V", "depends_on": ["tests", "docs"], "category": "validate"},
            {"id": "summary", "title": "S", "depends_on": ["validate"], "category": "summary"},
        ]})
        provider = _provider([payload])
        analysis = provider._fallback.analyze_requirement(Requirement("Build a URL shortener."))
        graph = provider.decompose(analysis)
        graph.validate_acyclic()
        self.assertIn("code", graph.by_id())

    def test_falls_back_on_malformed_json(self):
        # Two bad responses exhaust retries, then the provider must degrade gracefully.
        provider = _provider(["not json", "still not json"])
        analysis = provider.analyze_requirement(Requirement("Build a URL shortener."))
        self.assertEqual(analysis.domain, "url_shortener")  # deterministic fallback result
        self.assertGreaterEqual(provider.metrics.fallbacks, 1)

    def test_falls_back_on_invalid_plan_categories(self):
        bad_plan = json.dumps({"tasks": [{"id": "x", "category": "bogus", "depends_on": []}]})
        provider = _provider([bad_plan, bad_plan])
        analysis = provider._fallback.analyze_requirement(Requirement("Build a URL shortener."))
        graph = provider.decompose(analysis)
        # The deterministic plan is well-formed and acyclic.
        graph.validate_acyclic()
        self.assertGreaterEqual(provider.metrics.fallbacks, 1)


_VALID_BUNDLE = json.dumps({"files": [
    {"path": "pkg/__init__.py", "content": ""},
    {"path": "pkg/calc.py", "content": "def add(a, b):\n    return a + b\n"},
    {"path": "tests/test_calc.py",
     "content": ("import unittest\nfrom pkg.calc import add\n\n"
                 "class T(unittest.TestCase):\n"
                 "    def test_add(self):\n"
                 "        self.assertEqual(add(1, 2), 3)\n\n"
                 "if __name__ == '__main__':\n    unittest.main()\n")},
    {"path": "README.md", "content": "# Generated\n"},
]})

# Syntactically broken code must be rejected by the sandbox gate.
_BROKEN_BUNDLE = json.dumps({"files": [
    {"path": "pkg/bad.py", "content": "def broken(:\n    pass\n"},
    {"path": "tests/test_bad.py", "content": "import unittest\n"},
]})


class LLMCodegenTests(unittest.TestCase):
    def _prepare(self, provider):
        analysis = provider._fallback.analyze_requirement(
            Requirement("Build a scalable URL shortener service."))
        architecture = provider._fallback.design(analysis)
        return analysis, architecture

    def test_accepts_validated_model_authored_project(self):
        provider = _provider([_VALID_BUNDLE])
        analysis, architecture = self._prepare(provider)
        code = provider.generate_code(analysis, architecture)
        tests = provider.generate_tests(analysis, architecture, code)
        paths = {a.path for a in code} | {a.path for a in tests}
        self.assertIn("pkg/calc.py", paths)          # model-authored, not the template
        self.assertIn("tests/test_calc.py", paths)
        self.assertEqual(provider.metrics.fallbacks, 0)

    def test_falls_back_to_verified_template_on_broken_code(self):
        provider = _provider([_BROKEN_BUNDLE])
        analysis, architecture = self._prepare(provider)
        code = provider.generate_code(analysis, architecture)
        paths = {a.path for a in code}
        self.assertIn("url_shortener/service.py", paths)  # verified template took over
        self.assertGreaterEqual(provider.metrics.fallbacks, 1)


if __name__ == "__main__":
    unittest.main()
