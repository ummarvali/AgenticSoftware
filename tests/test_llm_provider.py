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

    def complete(self, system, user, *, json_mode=True, timeout=30.0, max_tokens=4096):
        self.calls += 1
        self.last_max_tokens = max_tokens
        self.last_timeout = timeout
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


class GuardrailTests(unittest.TestCase):
    def test_call_budget_degrades_to_deterministic_instead_of_spending(self):
        good = json.dumps({"kind": "greenfield", "intent": "x", "normalized_problem": "p",
                           "functional_requirements": [], "non_functional_requirements": [],
                           "ambiguities": [], "domain": "url_shortener", "confidence": 0.9})
        client = FakeClient([good, good, good])
        provider = LLMProvider(client, max_retries=1, backoff_s=0.0, max_calls=1,
                               max_cost_usd=100.0, enable_codegen=False)
        req = Requirement("Build a URL shortener.")
        provider.analyze_requirement(req)          # 1st call: allowed
        analysis = provider.analyze_requirement(req)   # 2nd: budget spent -> fallback
        self.assertEqual(client.calls, 1, "model must not be called past the budget")
        self.assertEqual(analysis.domain, "url_shortener")   # still a valid answer
        self.assertEqual(provider.metrics.fallbacks, 1)
        self.assertIn("budget", provider.metrics.calls[-1].error)

    def test_cost_budget_is_enforced(self):
        good = json.dumps({"kind": "greenfield", "intent": "x", "normalized_problem": "p",
                           "functional_requirements": [], "non_functional_requirements": [],
                           "ambiguities": [], "domain": "generic", "confidence": 0.5})
        client = FakeClient([good, good])
        provider = LLMProvider(client, max_retries=1, backoff_s=0.0, max_calls=99,
                               max_cost_usd=0.0, enable_codegen=False)
        provider.analyze_requirement(Requirement("Build something."))
        self.assertEqual(client.calls, 0)
        self.assertEqual(provider.metrics.fallbacks, 1)

    def test_generated_code_never_sees_secrets(self):
        import os
        from agentic_sdlc.tools.code_runner import scrubbed_env
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
        os.environ["SOME_TOKEN"] = "t"
        os.environ["HARMLESS_VAR"] = "ok"
        try:
            env = scrubbed_env()
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            self.assertNotIn("SOME_TOKEN", env)
            self.assertEqual(env["HARMLESS_VAR"], "ok")
            self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        finally:
            for k in ("ANTHROPIC_API_KEY", "SOME_TOKEN", "HARMLESS_VAR"):
                os.environ.pop(k, None)


class LenientParsingTests(unittest.TestCase):
    """Real models answer loosely ("201 Created"); a strict parser must not throw a whole
    stage away over it. (Seen in a live Claude run: design fell back on int('201 Created').)"""

    def test_design_accepts_textual_status_and_odd_types(self):
        payload = json.dumps({
            "overview": "o", "components": ["a"], "data_model": ["m"],
            "api": [
                {"method": "POST", "path": "/api/shorten", "summary": "s",
                 "status": "201 Created / 400 Bad Request / 409 Conflict"},
                {"method": "GET", "path": "/{code}", "summary": "r", "status": 302},
                {"method": "GET", "path": "/healthz", "summary": "h", "status": None},
            ],
            "decisions": ["d"], "tradeoffs": ["t"],
        })
        provider = _provider([payload])
        from agentic_sdlc.models import AnalysisResult, RequirementKind
        arch = provider.design(AnalysisResult(kind=RequirementKind.GREENFIELD, intent="i",
                                              normalized_problem="p"))
        self.assertEqual([e.status for e in arch.api], [201, 302, 200])
        self.assertEqual(provider.metrics.fallbacks, 0)

    def test_codegen_gets_a_large_output_budget(self):
        provider = _provider([json.dumps({"files": []})])
        from agentic_sdlc.models import AnalysisResult, Architecture, RequirementKind
        provider.generate_code(AnalysisResult(kind=RequirementKind.GREENFIELD, intent="i", normalized_problem="p"),
                               Architecture(overview="o", components=["c"]))
        self.assertGreaterEqual(provider._client.last_max_tokens, 32000)
        self.assertGreaterEqual(provider._client.last_timeout, 600)   # minutes, not seconds

    def test_non_json_reply_error_shows_what_came_back(self):
        provider = _provider(["Sure! Here is the design you asked for.", "still not json"])
        from agentic_sdlc.models import AnalysisResult, RequirementKind
        provider.design(AnalysisResult(kind=RequirementKind.GREENFIELD, intent="i", normalized_problem="p"))
        errs = [c.error for c in provider.metrics.calls if c.error]
        self.assertTrue(any("starts with: 'Sure! Here" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
