"""End-to-end orchestrator tests: full runs, recovery, and human halts."""

import tempfile
import unittest

from agentic_sdlc.hitl import ApprovalGate, Decision
from agentic_sdlc.llm.deterministic import DeterministicProvider
from agentic_sdlc.models import Task, TaskGraph
from agentic_sdlc.orchestrator import Orchestrator, OrchestratorConfig


class _RejectFirstGate(ApprovalGate):
    """Approves nothing — used to prove a human can stop the pipeline."""

    def review(self, stage, summary):
        return Decision(False, f"rejected at {stage}")


def _config(tmp, **kw):
    return OrchestratorConfig(output_root=tmp, verbose=False, **kw)


class EndToEndTests(unittest.TestCase):
    def test_url_shortener_full_run_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Orchestrator(_config(tmp)).run(
                "Build a scalable URL shortener service with APIs, persistence, and analytics."
            )
            self.assertIsNotNone(result.summary)
            self.assertIsNotNone(result.validation)
            self.assertTrue(result.validation.passed, result.validation.summary)
            paths = {a.path for a in result.artifacts}
            self.assertIn("url_shortener/service.py", paths)
            self.assertIn("openapi.yaml", paths)
            self.assertIn("ENGINEERING_SUMMARY.md", paths)

    def test_recovers_from_transient_fault(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Force the 'code' task to fail once; max_attempts=2 should recover.
            result = Orchestrator(_config(tmp, inject_fault={"code": 1})).run(
                "Build a URL shortener service."
            )
            self.assertTrue(result.validation and result.validation.passed)
            self.assertTrue(any(e["kind"] == "task_error" for e in result.events))
            self.assertGreaterEqual(result.metrics["run"]["retries"], 1)

    def test_run_metrics_always_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Orchestrator(_config(tmp)).run("Build a URL shortener service.")
            run = result.metrics["run"]
            self.assertIn("duration_s", run)
            self.assertGreater(run["tasks_ok"], 0)
            self.assertGreaterEqual(run["gates"], 3)  # clarify + plan + accept
            self.assertTrue(run["validation_passed"])

    def test_required_task_failure_halts(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Fail 'code' more times than allowed -> required task cannot recover.
            result = Orchestrator(_config(tmp, inject_fault={"code": 5})).run(
                "Build a URL shortener service."
            )
            halted = [e for e in result.events if e["kind"] == "halted"]
            self.assertTrue(halted)
            self.assertIsNone(result.summary)

    def test_human_rejection_halts(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Orchestrator(_config(tmp), gate=_RejectFirstGate()).run(
                "Build a URL shortener service."
            )
            self.assertIsNone(result.summary)
            self.assertTrue(any(e["kind"] == "halted" for e in result.events))

    def test_optional_task_degrades_without_halting(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Docs is optional: failing it repeatedly should degrade, not halt.
            result = Orchestrator(_config(tmp, inject_fault={"docs": 5})).run(
                "Build a URL shortener service."
            )
            self.assertIsNotNone(result.summary)
            self.assertTrue(any(e["kind"] == "degrade" for e in result.events))

    def test_validation_feedback_loop_repairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            # validation fails 'api contract present'. The repair agent must fix it and
            # re-validation must then pass — an agent-driven feedback loop.
            result = Orchestrator(_config(tmp)).run("Make the app faster.")
            self.assertTrue(result.validation and result.validation.passed)
            self.assertTrue(any(e["kind"] == "repair" for e in result.events))
            self.assertTrue(any(e["kind"] == "decision" for e in result.events))

    def test_architect_records_a_decision_rationale(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Orchestrator(_config(tmp)).run(
                "Build a scalable URL shortener service with persistence."
            )
            decisions = [e for e in result.events
                         if e["kind"] == "decision" and e.get("agent") == "Architect"]
            self.assertTrue(decisions)
            self.assertIn("sqlite", decisions[0]["message"].lower())

class ParallelExecutionTests(unittest.TestCase):
    """Independent tasks in one DAG level run concurrently, and it stays correct."""

    REQ = "Build a scalable URL shortener service with APIs, persistence, and analytics."

    def test_level_with_independent_tasks_runs_in_parallel(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Orchestrator(_config(tmp)).run(self.REQ)
            parallel = [e for e in result.events if e["kind"] == "parallel"]
            self.assertEqual(len(parallel), 1)
            self.assertEqual(sorted(parallel[0]["tasks"]), ["code", "docs"])
            self.assertEqual(result.metrics["run"]["parallel_levels"], 1)
            self.assertTrue(result.validation.passed)

    def test_parallel_and_sequential_produce_the_same_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            par = Orchestrator(_config(tmp)).run(self.REQ)
            seq = Orchestrator(_config(tmp, parallel=False)).run(self.REQ)
            self.assertEqual(seq.metrics["run"]["parallel_levels"], 0)
            self.assertEqual({a.path for a in par.artifacts}, {a.path for a in seq.artifacts})
            self.assertEqual(par.validation.summary, seq.validation.summary)

    def test_sibling_task_completes_before_a_parallel_level_halts(self):
        # 'code' is required and never recovers; 'docs' shares its level and must
        # still finish (no orphaned thread, no partial write) before the halt.
        with tempfile.TemporaryDirectory() as tmp:
            result = Orchestrator(_config(tmp, inject_fault={"code": 9})).run(self.REQ)
            self.assertTrue(any(e["kind"] == "halted" for e in result.events))
            self.assertTrue(any(e["kind"] == "docs" for e in result.events))
            self.assertIsNone(result.validation)


class _FineGrainedPlanProvider(DeterministicProvider):
    """Mimics a live model's plan: many tasks per category, some sharing a level.

    This is the shape a real LLM produced (22 tasks / 11 levels). Agents must treat the
    plan as *scope*, not as a request to redo the same work N times.
    """

    name = "fine-grained"

    def decompose(self, analysis):
        t = lambda i, cat, deps, title="": Task(i, title or i, "", depends_on=deps, category=cat)  # noqa: E731
        return TaskGraph(tasks=[
            t("design-architecture", "design", []),
            t("design-api", "design", ["design-architecture"]),
            t("design-data-model", "design", ["design-architecture"]),
            t("code-storage-layer", "code", ["design-api", "design-data-model"]),
            t("code-cache-layer", "code", ["code-storage-layer"]),
            t("code-short-code-generator", "code", ["code-storage-layer"]),
            t("code-redirect-endpoint", "code", ["code-cache-layer"]),
            t("tests-unit-core", "tests", ["code-storage-layer"]),
            t("tests-integration-api", "tests", ["code-redirect-endpoint"]),
            t("docs-api-reference", "docs", ["design-api"]),
            t("docs-architecture", "docs", ["design-architecture"]),
            t("validate-e2e", "validate", ["tests-integration-api", "tests-unit-core",
                                           "docs-api-reference", "docs-architecture"]),
            t("validate-resilience", "validate", ["validate-e2e"]),
            t("summary", "summary", ["validate-resilience"]),
        ])


class FineGrainedPlanTests(unittest.TestCase):
    REQ = "Build a scalable URL shortener service with APIs, persistence, and analytics."

    def test_agents_reuse_work_instead_of_duplicating_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            orch = Orchestrator(_config(tmp), provider=_FineGrainedPlanProvider())
            result = orch.run(self.REQ)
            self.assertTrue(result.validation.passed, result.validation.summary)
            paths = [a.path for a in result.artifacts]
            self.assertEqual(len(paths), len(set(paths)), "duplicate artifacts on the blackboard")
            self.assertEqual(len(paths), 15)                       # same as the 6-task plan
            self.assertEqual(sum(1 for e in result.events if e["kind"] == "design"), 1)
            reused = [e for e in result.events if e["kind"] == "skip"]
            self.assertGreaterEqual(len(reused), 8)                # 2 design + 3 code + 1 tests + 1 docs + 1 validate
            self.assertEqual(result.metrics["run"]["reused"], len(reused))
            self.assertEqual(result.metrics["run"]["tasks_ok"], 14)  # every task still completes


class EngineeringSummaryTests(unittest.TestCase):
    """The final output must carry every item the brief lists."""

    def test_summary_contains_plan_validation_risks_tradeoffs_assumptions_limitations(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Orchestrator(_config(tmp)).run(
                "Build a scalable URL shortener service with APIs, persistence, and analytics.")
            s = result.summary
            self.assertTrue(s.implementation_plan and s.implementation_plan[0].startswith("Level 0:"))
            self.assertTrue(s.rationale)
            self.assertEqual(len(s.artifacts), 14)   # the summary does not list itself
            self.assertEqual(len(s.validation_checks), 4)
            self.assertTrue(all(c["passed"] for c in s.validation_checks))
            self.assertGreaterEqual(len(s.validation_approach), 5)
            self.assertTrue(s.risks and s.tradeoffs and s.assumptions and s.limitations)
            self.assertEqual(s.monitoring["provider"], "deterministic")
            md = next(a.content for a in result.artifacts if a.path == "ENGINEERING_SUMMARY.md")
            for heading in ("## Implementation Plan", "## Rationale", "## Generated Artifacts",
                            "## Validation", "## Run Monitoring", "## Risks", "## Trade-offs",
                            "## Assumptions", "## Limitations"):
                self.assertIn(heading, md)


if __name__ == "__main__":
    unittest.main()
