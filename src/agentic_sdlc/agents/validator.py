"""Validator agent — the guardrail that actually verifies the generated work.

It compiles every generated Python file, executes the generated test suite in a
sandboxed subprocess, checks that the promised contract/documentation artifacts
exist, and compiles a risk register. Its :class:`ValidationReport` is what a human
uses to decide whether to accept the run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_sdlc.agents.base import Agent, AgentDecision
from agentic_sdlc.models import Check, Task, ValidationReport
from agentic_sdlc.orchestrator.state import AgentContext

#: Checks the RepairAgent knows how to fix automatically. Anything else that fails
#: (e.g. a compile error or a failing test) requires human attention.
REPAIRABLE_CHECKS = {"api contract present", "documentation present"}


class ValidatorAgent(Agent):
    name = "Validator"
    category = "validate"

    def decide(self, ctx: AgentContext, obs: dict[str, Any]) -> AgentDecision:
        return AgentDecision("validate", "compile code, run tests, check contract & docs")

    def act(self, ctx: AgentContext, task: Task, decision: AgentDecision) -> None:
        bb = ctx.blackboard
        out = Path(ctx.output_dir)
        checks: list[Check] = []

        # 1) Static: every generated .py file must compile.
        py_files = [out / a.path for a in bb.all_artifacts() if a.path.endswith(".py")]
        compile_results = ctx.tools.runner.compile_python(py_files)
        failed = [c for c in compile_results if not c.ok]
        checks.append(Check(
            "code compiles",
            not failed,
            "all files compiled" if not failed
            else "; ".join(f"{c.path}: {c.error}" for c in failed),
        ))

        # 2) Dynamic: the generated test suite must pass.
        test_result = ctx.tools.runner.run_unittests(out)
        checks.append(Check(
            "tests pass",
            test_result.ok,
            self._tail(test_result.output),
        ))

        # 3) Contract present when the design exposes an API.
        has_api = bool(bb.architecture and bb.architecture.api)
        contract_exists = (out / "openapi.yaml").exists()
        checks.append(Check(
            "api contract present",
            (contract_exists or not has_api),
            "openapi.yaml found" if contract_exists else "no API contract required",
        ))

        # 4) Documentation present.
        docs_exist = any(a.kind == "docs" for a in bb.docs)
        checks.append(Check("documentation present", docs_exist,
                            "docs generated" if docs_exist else "no docs generated"))

        report = ValidationReport(checks=checks, risks=self._risks(bb))
        bb.validation = report
        bb.log("validation", report.summary,
               passed=report.passed, checks=[c.name for c in checks if not c.passed])
        ctx.emit(self.name, f"{report.summary}"
                            f"{' — FAILURES PRESENT' if not report.passed else ''}")

        # A compile failure is unrecoverable garbage; surface it so the orchestrator
        # can retry or halt. Failing tests are recorded but do not raise, because the
        # report itself is the deliverable a human reviews.
        if failed:
            raise RuntimeError(f"generated code failed to compile: {failed[0].error}")

    @staticmethod
    def _tail(text: str, lines: int = 3) -> str:
        return "\n".join(text.strip().splitlines()[-lines:]) if text.strip() else ""

    @staticmethod
    def _risks(bb) -> list[str]:
        risks = list(bb.architecture.tradeoffs) if bb.architecture else []
        risks += [
            "Prototype persistence defaults to in-memory; data is lost on restart "
            "unless the SQLite backend is selected.",
            "No authentication/rate limiting on link creation by default (abuse risk).",
            "Generated tests cover core paths; add load/security tests before production.",
        ]
        return risks
