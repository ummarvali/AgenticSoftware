"""Repair agent — closes the validation feedback loop.

When the validator reports a *repairable* finding (a missing API contract or missing
documentation), the orchestrator routes control here. The agent perceives the failing
checks, decides which it can fix, acts to produce the missing artifact, and the
orchestrator then re-validates. This is genuine agent-driven recovery — a decision
made from observed state, not a blind retry of identical work.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agentic_sdlc.agents.base import Agent, AgentDecision
from agentic_sdlc.agents.validator import REPAIRABLE_CHECKS
from agentic_sdlc.models import Artifact, Task
from agentic_sdlc.orchestrator.state import AgentContext


class RepairAgent(Agent):
    name = "Repair"
    category = "repair"

    def perceive(self, ctx: AgentContext, task: Task) -> dict[str, Any]:
        report = ctx.blackboard.validation
        failing = [c.name for c in report.checks if not c.passed] if report else []
        return {"repairable": [c for c in failing if c in REPAIRABLE_CHECKS]}

    def decide(self, ctx: AgentContext, obs: dict[str, Any]) -> AgentDecision:
        fixes = obs["repairable"]
        if not fixes:
            return AgentDecision("no-op", "no auto-repairable findings", proceed=False)
        return AgentDecision("repair", f"auto-fixing: {', '.join(fixes)}",
                             params={"fixes": fixes})

    def act(self, ctx: AgentContext, task: Task, decision: AgentDecision) -> None:
        for name in decision.params["fixes"]:
            handler = self._HANDLERS.get(name)
            if handler:
                handler(self, ctx)
        ctx.blackboard.log("repair", f"applied fixes: {decision.params['fixes']}")
        ctx.emit(self.name, f"applied fixes: {', '.join(decision.params['fixes'])}")

    # -- fixers ------------------------------------------------------------ #

    def _add_contract(self, ctx: AgentContext) -> None:
        """Synthesize ``openapi.yaml`` from the (model-authored) API design.

        Only endpoints the generated slice actually serves are documented; methods
        sharing a path are grouped under one path item (duplicate keys would be
        silently dropped by any YAML parser); ``{param}`` segments are declared as
        path parameters; strings are JSON-quoted, which is valid YAML."""

        bb = ctx.blackboard
        implemented, design_only = bb.endpoint_coverage()
        endpoints = implemented or (bb.architecture.api if bb.architecture else [])
        by_path: dict[str, list] = {}
        for e in endpoints:
            by_path.setdefault(e.path, []).append(e)

        q = json.dumps
        lines = ["openapi: 3.0.3", "info:", "  title: Generated API", "  version: 1.0.0",
                 "  description: " + q("Synthesized by the Repair agent from the API design; "
                                       "lists only endpoints the generated slice serves."),
                 "paths:"]
        if design_only and implemented:
            lines.append("  # design-level endpoint(s) not in this slice: "
                         + ", ".join(f"{e.method} {e.path}" for e in design_only))
        seen: set[tuple[str, str]] = set()
        for path, eps in by_path.items():
            lines.append(f"  {q(path)}:")
            params = re.findall(r"\{([^}/]+)\}", path)
            if params:
                lines.append("    parameters:")
                for name in params:
                    lines += [f"      - name: {q(name)}", "        in: path",
                              "        required: true", "        schema: { type: string }"]
            for e in eps:
                method = e.method.lower()
                if (path, method) in seen:
                    continue
                seen.add((path, method))
                lines += [f"    {method}:",
                          f"      summary: {q(e.summary or '')}",
                          "      responses:",
                          f"        {q(str(e.status))}:",
                          f"          description: {q(e.response or 'Success')}"]
        if not by_path:
            lines.append("  {}")
        artifact = Artifact("openapi.yaml", "\n".join(lines) + "\n", "contract")
        bb.merge(bb.code, [artifact])
        ctx.tools.artifacts.write(artifact)

    def _add_readme(self, ctx: AgentContext) -> None:
        """Synthesize a README: overview, served endpoints, how to start and test it."""

        bb = ctx.blackboard
        overview = bb.architecture.overview if bb.architecture else ""
        implemented, _ = bb.endpoint_coverage()
        entry = self._entry_point(bb.code)
        parts = ["# Generated Service", "",
                 "> Synthesized by the Repair agent from the design (the model's code bundle "
                 "did not include a README).", "", overview, ""]
        if implemented:
            parts += ["## Endpoints served by this slice", "",
                      *[f"- `{e.method} {e.path}` — {e.summary}" for e in implemented],
                      "", "Full contract: `openapi.yaml`.", ""]
        parts += ["## Run", ""]
        parts += ([f"`python {entry}` — see that file for the default host, port and "
                   "storage path." if entry.endswith(".py") else
                   f"`python -m {entry}` — see that module for the default host, port and "
                   "storage path.", ""]
                  if entry else ["No runnable entry point was detected; import the package "
                                 "and serve its WSGI/HTTP app.", ""])
        parts += ["## Tests", "", "`python -m unittest discover -s tests`", "",
                  "The engineering record for this run is `ENGINEERING_SUMMARY.md`.", ""]
        artifact = Artifact("README.md", "\n".join(parts), "docs")
        bb.merge(bb.docs, [artifact])
        ctx.tools.artifacts.write(artifact)

    @staticmethod
    def _entry_point(code: list[Artifact]) -> str:
        """Best-effort: the module that starts a server under ``__main__``."""

        candidates = [a for a in code if a.path.endswith(".py")
                      and "__main__" in a.content
                      and any(m in a.content for m in ("serve_forever", "make_server", "run("))]
        if not candidates:
            return ""
        best = sorted(candidates, key=lambda a: (a.path.count("/"), a.path))[0]
        if "/" not in best.path:
            return best.path
        return best.path[:-3].replace("/", ".")

    _HANDLERS = {
        "api contract present": _add_contract,
        "documentation present": _add_readme,
    }
