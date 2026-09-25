"""Human-in-the-loop approval gates — the "controlled" in controlled autonomy.

Agents execute independently, but the pipeline pauses at defined checkpoints so a
human can review and approve or reject before the run continues. The same gates
support a non-interactive ``auto`` mode (documented assumptions are applied) so the
system is scriptable and testable — but auto mode never *accepts* a run whose
validation failed (see ``Orchestrator._accept``), and the interactive gate fails closed.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class Decision:
    approved: bool
    note: str = ""


class ApprovalGate(abc.ABC):
    """Strategy for resolving a checkpoint decision."""

    @abc.abstractmethod
    def review(self, stage: str, summary: str) -> Decision:
        ...


class AutoApprove(ApprovalGate):
    """Non-interactive: approve every gate and record that autonomy was used."""

    def review(self, stage: str, summary: str) -> Decision:
        return Decision(True, "auto-approved (non-interactive mode)")


class ConsoleApproval(ApprovalGate):
    """Interactive: prompt on the console at each checkpoint."""

    def review(self, stage: str, summary: str) -> Decision:
        print("\n" + "=" * 70)
        print(f"HUMAN CHECKPOINT — {stage}")
        print("-" * 70)
        print(summary)
        print("-" * 70)
        try:
            answer = input("Approve and continue? [Y/n] ").strip().lower()
        except EOFError:
            # No TTY / closed input: fail closed. Use auto mode for unattended runs.
            return Decision(False, "no input available; rejected (fail closed)")
        if answer in ("", "y", "yes"):
            return Decision(True, "approved by human")
        return Decision(False, "rejected by human")
