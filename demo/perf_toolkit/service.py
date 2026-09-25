"""Orchestrates profiling runs, optimizations, regression checks and comparisons."""

from __future__ import annotations

from typing import Optional

from . import profiler
from .optimizations import build_registry
from .storage import build_storage
from .workload import DemoWorkload

PROFILE_ITERATIONS = 50
BENCHMARK_ITERATIONS = 30


class NotFoundError(LookupError):
    """Raised when a requested run or optimization id does not exist."""


class PerfToolkitService:
    """Facade used by both the HTTP API and tests."""

    def __init__(self, storage=None, workload: Optional[DemoWorkload] = None) -> None:
        self.storage = storage or build_storage()
        self.workload = workload or DemoWorkload()
        self.optimizations = build_registry(self.workload)

    # ---- runs ---------------------------------------------------------
    def create_run(self, iterations: int = PROFILE_ITERATIONS) -> dict:
        result = profiler.profile_workload(self.workload.run_once, iterations=iterations)
        run_id = self.storage.save_run(
            result["iterations"], result["total_time"], result["avg_time"], result["bottlenecks"]
        )
        return self.get_run(run_id)

    def get_run(self, run_id: int) -> dict:
        run = self.storage.get_run(run_id)
        if run is None:
            raise NotFoundError(f"run {run_id} not found")
        return run

    def get_bottlenecks(self, run_id: int) -> list:
        return self.get_run(run_id)["bottlenecks"]

    # ---- optimizations --------------------------------------------------
    def list_optimizations(self) -> list:
        return [
            {
                "id": opt.id,
                "name": opt.name,
                "description": opt.description,
                "applied": opt.applied,
            }
            for opt in self.optimizations.values()
        ]

    def apply_optimization(self, opt_id: str) -> dict:
        opt = self._get_optimization(opt_id)
        before = profiler.time_call(self.workload.run_once, iterations=BENCHMARK_ITERATIONS)
        opt.apply()
        opt.applied = True
        after = profiler.time_call(self.workload.run_once, iterations=BENCHMARK_ITERATIONS)
        comparison_id = self.storage.save_comparison(opt.id, "apply", before, after)
        return self._comparison_summary(comparison_id, opt, before, after, "applied")

    def rollback_optimization(self, opt_id: str) -> dict:
        opt = self._get_optimization(opt_id)
        before = profiler.time_call(self.workload.run_once, iterations=BENCHMARK_ITERATIONS)
        opt.rollback()
        opt.applied = False
        after = profiler.time_call(self.workload.run_once, iterations=BENCHMARK_ITERATIONS)
        comparison_id = self.storage.save_comparison(opt.id, "rollback", before, after)
        return self._comparison_summary(comparison_id, opt, before, after, "rolled_back")

    def _get_optimization(self, opt_id: str):
        opt = self.optimizations.get(opt_id)
        if opt is None:
            raise NotFoundError(f"optimization {opt_id!r} not found")
        return opt

    @staticmethod
    def _comparison_summary(comparison_id: int, opt, before: float, after: float, status: str) -> dict:
        improvement_pct = ((before - after) / before * 100.0) if before > 0 else 0.0
        return {
            "comparison_id": comparison_id,
            "optimization_id": opt.id,
            "status": status,
            "before_time": before,
            "after_time": after,
            "improvement_pct": improvement_pct,
        }

    # ---- comparisons ----------------------------------------------------
    def list_comparisons(self) -> list:
        return self.storage.list_comparisons()

    # ---- regression tests -------------------------------------------------
    def run_regression_tests(self) -> dict:
        """Re-check core correctness of the workload's service, so applying or
        rolling back an optimization can be verified to not change behaviour."""

        svc = self.workload.service
        checks = []

        def check(name: str, fn) -> None:
            try:
                fn()
                checks.append({"name": name, "passed": True, "error": None})
            except Exception as exc:  # noqa: BLE001 - want to record any failure
                checks.append({"name": name, "passed": False, "error": str(exc)})

        def shorten_and_resolve() -> None:
            record = svc.shorten("https://example.com/regression-check")
            resolved = svc.resolve(record.code)
            assert resolved == "https://example.com/regression-check"

        def stats_counts_clicks() -> None:
            record = svc.shorten("https://example.com/regression-stats")
            svc.resolve(record.code)
            stats = svc.stats(record.code)
            assert stats is not None and stats["clicks"] >= 1

        def unknown_code_resolves_to_none() -> None:
            assert svc.resolve("does-not-exist-code") is None

        check("shorten_and_resolve", shorten_and_resolve)
        check("stats_counts_clicks", stats_counts_clicks)
        check("unknown_code_resolves_to_none", unknown_code_resolves_to_none)

        return {"passed": all(c["passed"] for c in checks), "checks": checks}
