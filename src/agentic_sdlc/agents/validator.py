"""Validator agent — the guardrail that actually verifies the generated work.

It compiles every generated Python file, executes the generated test suite in a
sandboxed subprocess, checks that the promised contract/documentation artifacts
exist, and compiles a risk register. Its :class:`ValidationReport` is what a human
uses to decide whether to accept the run.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from agentic_sdlc.agents.base import Agent, AgentDecision
from agentic_sdlc.models import Check, Task, ValidationReport
from agentic_sdlc.orchestrator.state import AgentContext
from agentic_sdlc.tools import repo as repo_tool
from agentic_sdlc.tools.static_check import Finding, scan_file, scan_tree

#: Checks the RepairAgent knows how to fix automatically. Anything else that fails
#: (e.g. a compile error or a failing test) requires human attention.
REPAIRABLE_CHECKS = {"api contract present", "documentation present"}


class ValidatorAgent(Agent):
    name = "Validator"
    category = "validate"

    def perceive(self, ctx: AgentContext, task: Task) -> dict[str, Any]:
        bb = ctx.blackboard
        return {"fingerprint": bb.fingerprint(),
                "validated": bb.validated_fingerprint if bb.validation else "",
                "task": task.id}

    def decide(self, ctx: AgentContext, obs: dict[str, Any]) -> AgentDecision:
        if obs["validated"] and obs["validated"] == obs["fingerprint"]:
            return AgentDecision(
                "reuse", f"artifact set unchanged since the last report; "
                         f"'{obs['task']}' needs no re-run",
                proceed=False,
            )
        return AgentDecision("validate", "compile code, run tests, check contract & docs")

    def act(self, ctx: AgentContext, task: Task, decision: AgentDecision) -> None:
        if ctx.blackboard.change_mode:
            with tempfile.TemporaryDirectory() as tmp:
                self._validate(ctx, Path(tmp) / "overlay")
            return
        self._validate(ctx, None)

    def _validate(self, ctx: AgentContext, overlay: Path | None) -> None:
        """Greenfield: validate the generated project in the output folder. Change mode:
        lay the change set over a throwaway copy of the repository and validate that —
        the repository's own tests must still pass with the change applied."""

        bb = ctx.blackboard
        out = Path(ctx.output_dir)
        checks: list[Check] = []
        changed = [a for a in bb.all_artifacts()
                   if a.kind != "change" and a.path != "ENGINEERING_SUMMARY.md"]
        if overlay is not None:
            repo_tool.materialize_overlay(bb.requirement.repo_path, changed, overlay)
            out = overlay

        # 1) Static: every generated (or changed) .py file must compile.
        py_files = [out / a.path for a in changed if a.path.endswith(".py")]
        compile_results = ctx.tools.runner.compile_python(py_files)
        failed = [c for c in compile_results if not c.ok]
        checks.append(Check(
            "code compiles",
            not failed,
            "all files compiled" if not failed
            else "; ".join(f"{c.path}: {c.error}" for c in failed),
        ))

        # 2) Static safety scan BEFORE anything is executed: dangerous calls, imports
        #    outside the standard library, hard-coded secrets, stdlib-shadowing files.
        findings = scan_tree(out) if overlay is None else self._scan_changes(out, changed)
        high = [f for f in findings if f.severity == "high"]

        # 3) Dynamic: the generated test suite must pass. Code with high-severity
        #    findings is never executed.
        if high:
            checks.append(Check("tests pass", False,
                                "not executed: the static safety scan found high-severity issues"))
        else:
            test_result = ctx.tools.runner.run_unittests(out)
            checks.append(Check(
                "tests pass",
                test_result.ok,
                self._test_detail(test_result.output),
            ))

        # 4) Contract present when the design exposes an API.
        has_api = bool(bb.architecture and bb.architecture.api)
        contract_exists = (out / "openapi.yaml").exists()
        contract_detail = ("openapi.yaml found" if contract_exists
                           else ("openapi.yaml missing" if has_api else "no API contract required"))
        if overlay is not None and contract_exists:
            # Change mode: say whether the change set kept the contract in step, so a
            # reviewer sees an API change that left openapi.yaml untouched.
            touched = any(a.path == "openapi.yaml" for a in changed)
            contract_detail += (" (updated by this change set)" if touched
                                else " (not changed by this change set: confirm the API is unchanged)")
        checks.append(Check("api contract present", (contract_exists or not has_api), contract_detail))

        # 5) Documentation present.
        docs_exist = any(a.kind == "docs" for a in bb.docs) or (
            overlay is not None and (out / "README.md").exists())
        checks.append(Check("documentation present", docs_exist,
                            "docs generated" if docs_exist else "no docs generated"))

        # 6) Change mode only: a change set was actually proposed.
        if overlay is not None:
            checks.append(Check(
                "change set present", bool(changed),
                f"{len(changed)} file(s) changed; repository tests re-run with the change applied"
                if changed else
                "no change authored — the offline engine only authors changes it knows "
                "exactly (rate limiting on its own layout); run with --provider claude"))

        checks.append(Check(
            "static safety scan",
            not high,
            (f"{len(findings)} finding(s), none high-severity" if findings else "no findings")
            if not high else "; ".join(str(f) for f in high[:5]),
        ))

        report = ValidationReport(checks=checks, risks=self._risks(bb))
        bb.validation = report
        # A report that found uncompilable code is never reused: a retry must re-check
        # (and re-raise) rather than accept the failed report as "unchanged".
        bb.validated_fingerprint = "" if failed else bb.fingerprint()
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
    def _scan_changes(root: Path, changed) -> list:
        """Scan only what the change touches: the repository itself is not under review,
        but a changed file must not add dangerous calls, and a new top-level module must
        not shadow the standard library."""

        local = {p.name.removesuffix(".py") for p in root.iterdir()}
        out = []
        for a in changed:
            if a.path.endswith(".py"):
                out += [Finding(a.path, f.line, f.severity, f.rule, f.detail)
                        for f in scan_file(root / a.path, local_packages=local)]
        changed_paths = {a.path for a in changed}
        out += [f for f in scan_tree(root) if f.rule == "stdlib-shadowing" and f.path.replace("\\", "/") in changed_paths]
        return out

    @staticmethod
    def _test_detail(output: str) -> str:
        """The unittest verdict ("Ran N tests ... OK"), with interpreter warnings
        emitted by the generated suite counted rather than pasted; on failure the
        tail of the output (the failure text) is kept."""
        lines = [l.strip() for l in output.strip().splitlines() if l.strip()]
        ran = next((l for l in lines if l.startswith("Ran ")), "")
        verdict = next((l for l in reversed(lines) if l.startswith(("OK", "FAILED"))), "")
        if not (ran and verdict and verdict.startswith("OK")):
            return ValidatorAgent._tail(output)
        warnings = sum(1 for l in lines if "Warning:" in l)
        note = f" ({warnings} interpreter warning(s) emitted by the generated tests)" if warnings else ""
        return f"{ran} — {verdict}{note}"

    @staticmethod
    def _tail(text: str, lines: int = 3) -> str:
        return "\n".join(text.strip().splitlines()[-lines:]) if text.strip() else ""

    @staticmethod
    def _risks(bb) -> list[str]:
        """Standing risks of the generated slice, derived from what was actually
        produced (not a copy of the design trade-offs, which the summary lists
        separately)."""
        code = "\n".join(a.content.lower() for a in bb.all_artifacts() if a.kind in ("code", "config"))
        if bb.change_mode:
            code += "\n" + "\n".join(t.lower() for p, t in bb.repo_files.items() if p.endswith(".py"))
        durable = "sqlite" in code
        in_memory = any(m in code for m in ("inmemory", "in_memory", "in-memory", ":memory:"))
        has_auth = any(m in code for m in ("api_key", "apikey", "x-api-key", "authorization", "bearer"))
        has_limit = any(m in code for m in ("rate_limit", "ratelimit", "token_bucket", "429"))
        if has_auth and has_limit:
            abuse = ("Authentication and rate limiting in the generated slice are in-process "
                     "prototypes: state resets on restart and is not shared across instances.")
        elif has_auth:
            abuse = ("Authentication in the generated slice is an in-process prototype; "
                     "rate limiting is not enforced on write endpoints.")
        elif has_limit:
            abuse = ("Rate limiting in the generated slice is in-process (resets on restart, "
                     "not shared across instances); write endpoints are unauthenticated.")
        else:
            abuse = "No authentication or rate limiting on write endpoints by default (abuse risk)."
        return [
            ("Persistence: an in-memory store (data lost on restart) or SQLite (single file, "
             "single node) where configured; a multi-node deployment needs an external database."
             if durable and in_memory else
             "Persistence is SQLite (single file, single node); a multi-node deployment "
             "needs an external database." if durable else
             "Prototype persistence is in-memory unless a durable backend is configured; "
             "data is lost on restart."),
            abuse,
            "Generated tests cover core paths; add load/security tests before production.",
        ]
