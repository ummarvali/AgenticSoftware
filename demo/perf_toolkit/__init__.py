"""Lightweight, stdlib-only performance profiling & optimization toolkit.

Instruments the ``url_shortener`` demo application's backend/API and SQLite
storage layer, records profiling runs, identifies bottlenecks, applies
targeted, reversible code-level optimizations, and produces measurable
before/after comparison reports. Storage is SQLite with an in-memory dict
fallback; the HTTP API is served with ``http.server`` only.
"""

from .service import PerfToolkitService, NotFoundError
from .workload import DemoWorkload

__all__ = ["PerfToolkitService", "NotFoundError", "DemoWorkload"]
