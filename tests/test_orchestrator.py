"""End-to-end orchestrator tests: full runs, recovery, and human halts."""

import tempfile
import unittest

from agentic_sdlc.hitl import ApprovalGate, Decision
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


if __name__ == "__main__":
    unittest.main()
