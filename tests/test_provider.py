"""Tests for the deterministic provider: classification, pack selection, planning."""

import unittest

from agentic_sdlc.llm.deterministic import DeterministicProvider
from agentic_sdlc.models import Requirement, RequirementKind


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.provider = DeterministicProvider()

    def test_greenfield_url_shortener(self):
        analysis = self.provider.analyze_requirement(
            Requirement("Build a scalable URL shortener service with APIs and analytics.")
        )
        self.assertEqual(analysis.kind, RequirementKind.GREENFIELD)
        self.assertEqual(analysis.domain, "url_shortener")
        self.assertGreater(len(analysis.functional_requirements), 0)

    def test_brownfield_detected(self):
        analysis = self.provider.analyze_requirement(
            Requirement("Add rate limiting to the existing URL shortener API.")
        )
        self.assertEqual(analysis.kind, RequirementKind.BROWNFIELD)

    def test_ambiguous_detected(self):
        analysis = self.provider.analyze_requirement(Requirement("Make the app faster."))
        self.assertEqual(analysis.kind, RequirementKind.AMBIGUOUS)
        self.assertTrue(analysis.ambiguities)

    def test_repo_path_forces_brownfield(self):
        analysis = self.provider.analyze_requirement(
            Requirement("Build a new reporting feature.", repo_path=".")
        )
        self.assertEqual(analysis.kind, RequirementKind.BROWNFIELD)


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.provider = DeterministicProvider()

    def test_greenfield_plan_has_no_impact_task(self):
        analysis = self.provider.analyze_requirement(Requirement("Build a URL shortener."))
        graph = self.provider.decompose(analysis)
        categories = {t.category for t in graph.tasks}
        self.assertNotIn("codebase_impact", categories)
        self.assertIn("design", categories)
        self.assertIn("validate", categories)

    def test_brownfield_plan_injects_impact_before_code(self):
        analysis = self.provider.analyze_requirement(
            Requirement("Refactor the existing URL shortener store.")
        )
        graph = self.provider.decompose(analysis)
        by_id = graph.by_id()
        self.assertIn("impact", by_id)
        self.assertIn("impact", by_id["code"].depends_on)

    def test_plan_is_acyclic(self):
        analysis = self.provider.analyze_requirement(Requirement("Build a URL shortener."))
        self.provider.decompose(analysis).validate_acyclic()


if __name__ == "__main__":
    unittest.main()
