"""Registry of known, reversible, code-level optimizations.

Each :class:`Optimization` targets a specific workload instance and can be
applied and rolled back independently — no new infrastructure is introduced,
only configuration of the existing code (see ``url_shortener.store.SqliteStore``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict


@dataclass
class Optimization:
    id: str
    name: str
    description: str
    apply: Callable[[], None]
    rollback: Callable[[], None]
    applied: bool = False


def build_registry(workload) -> Dict[str, Optimization]:
    """Build the optimizations available for ``workload`` (a DemoWorkload)."""

    def apply_batched_commits() -> None:
        workload.store.set_autocommit(False)

    def rollback_batched_commits() -> None:
        workload.store.set_autocommit(True)

    registry: Dict[str, Optimization] = {
        "sqlite-batched-commits": Optimization(
            id="sqlite-batched-commits",
            name="Batch SQLite commits",
            description=(
                "Defers the commit issued on every write (link creation, id "
                "allocation, click recording) so writes are batched instead "
                "of flushed one at a time, reducing per-request database "
                "overhead for write-heavy workloads. Reversible via rollback, "
                "which immediately flushes any pending writes."
            ),
            apply=apply_batched_commits,
            rollback=rollback_batched_commits,
        ),
    }
    return registry
