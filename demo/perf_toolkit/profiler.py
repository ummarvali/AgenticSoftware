"""Profiling helpers built entirely on the standard library.

``profile_workload`` runs a callable repeatedly under ``cProfile`` and
extracts the hottest functions (by cumulative time) as bottleneck candidates.
``time_call`` is a lower-overhead wall-clock timer used for quick before/after
comparisons when applying or rolling back an optimization.
"""

from __future__ import annotations

import cProfile
import io
import pstats
import time


def profile_workload(func, iterations: int = 50, top_n: int = 10) -> dict:
    if iterations <= 0:
        raise ValueError("iterations must be a positive integer")

    profiler = cProfile.Profile()
    start = time.perf_counter()
    profiler.enable()
    for _ in range(iterations):
        func()
    profiler.disable()
    total_time = time.perf_counter() - start
    avg_time = total_time / iterations

    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream)
    stats.sort_stats("cumulative")

    bottlenecks = []
    for func_key, raw in stats.stats.items():
        filename, lineno, funcname = func_key
        _cc, nc, tt, ct, _callers = raw
        bottlenecks.append(
            {
                "function": funcname,
                "file": filename,
                "line": lineno,
                "calls": nc,
                "total_time": tt,
                "cumulative_time": ct,
            }
        )
    bottlenecks.sort(key=lambda b: b["cumulative_time"], reverse=True)

    return {
        "iterations": iterations,
        "total_time": total_time,
        "avg_time": avg_time,
        "bottlenecks": bottlenecks[:top_n],
    }


def time_call(func, iterations: int = 30) -> float:
    """Return the total wall-clock time to call ``func`` ``iterations`` times."""
    if iterations <= 0:
        raise ValueError("iterations must be a positive integer")
    start = time.perf_counter()
    for _ in range(iterations):
        func()
    return time.perf_counter() - start
