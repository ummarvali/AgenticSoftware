"""Persistence for profiling runs and before/after comparison reports.

Uses SQLite (standard library) by default, falling back to an in-memory dict
implementation with the same interface if SQLite cannot be used for any
reason — matching the design's "SQLite or in-memory dict fallback" contract.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    iterations INTEGER NOT NULL,
    total_time REAL NOT NULL,
    avg_time REAL NOT NULL,
    bottlenecks TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comparisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    optimization_id TEXT NOT NULL,
    action TEXT NOT NULL,
    before_time REAL NOT NULL,
    after_time REAL NOT NULL,
    improvement_pct REAL NOT NULL
);
"""


def _improvement_pct(before: float, after: float) -> float:
    if before <= 0:
        return 0.0
    return (before - after) / before * 100.0


class SqliteStorage:
    """Durable storage for runs/comparisons, backed by SQLite."""

    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def save_run(self, iterations: int, total_time: float, avg_time: float, bottlenecks: list) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO runs(created_at, iterations, total_time, avg_time, bottlenecks) "
                "VALUES (?, ?, ?, ?, ?)",
                (time.time(), iterations, total_time, avg_time, json.dumps(bottlenecks)),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def get_run(self, run_id: int) -> Optional[dict]:
        row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._run_to_dict(row) if row else None

    def save_comparison(self, optimization_id: str, action: str, before_time: float, after_time: float) -> int:
        improvement_pct = _improvement_pct(before_time, after_time)
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO comparisons(created_at, optimization_id, action, before_time, "
                "after_time, improvement_pct) VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), optimization_id, action, before_time, after_time, improvement_pct),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_comparisons(self) -> list:
        rows = self._conn.execute("SELECT * FROM comparisons ORDER BY id ASC").fetchall()
        return [self._comparison_to_dict(r) for r in rows]

    @staticmethod
    def _run_to_dict(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "iterations": row["iterations"],
            "total_time": row["total_time"],
            "avg_time": row["avg_time"],
            "bottlenecks": json.loads(row["bottlenecks"]),
        }

    @staticmethod
    def _comparison_to_dict(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "optimization_id": row["optimization_id"],
            "action": row["action"],
            "before_time": row["before_time"],
            "after_time": row["after_time"],
            "improvement_pct": row["improvement_pct"],
        }


class DictStorage:
    """In-memory fallback storage, used only if SQLite is unavailable."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[int, dict] = {}
        self._run_seq = 0
        self._comparisons: list = []
        self._comparison_seq = 0

    def save_run(self, iterations: int, total_time: float, avg_time: float, bottlenecks: list) -> int:
        with self._lock:
            self._run_seq += 1
            self._runs[self._run_seq] = {
                "id": self._run_seq,
                "created_at": time.time(),
                "iterations": iterations,
                "total_time": total_time,
                "avg_time": avg_time,
                "bottlenecks": list(bottlenecks),
            }
            return self._run_seq

    def get_run(self, run_id: int) -> Optional[dict]:
        return self._runs.get(run_id)

    def save_comparison(self, optimization_id: str, action: str, before_time: float, after_time: float) -> int:
        with self._lock:
            self._comparison_seq += 1
            self._comparisons.append(
                {
                    "id": self._comparison_seq,
                    "created_at": time.time(),
                    "optimization_id": optimization_id,
                    "action": action,
                    "before_time": before_time,
                    "after_time": after_time,
                    "improvement_pct": _improvement_pct(before_time, after_time),
                }
            )
            return self._comparison_seq

    def list_comparisons(self) -> list:
        return list(self._comparisons)


def build_storage(path: str = ":memory:"):
    """Return a SQLite-backed storage, or an in-memory fallback if that fails."""
    try:
        return SqliteStorage(path)
    except Exception:
        return DictStorage()
