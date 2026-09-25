"""Persistence layer for links and click events.

Two interchangeable backends implement the same :class:`Store` protocol:

* :class:`InMemoryStore` — zero-setup, used by tests and for local demos.
* :class:`SqliteStore` — durable storage on the Python standard library only.

Swapping backends changes durability without touching the service or API layers.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class LinkRecord:
    """A single shortened link."""

    code: str
    long_url: str
    created_at: float
    expires_at: Optional[float] = None

    def is_expired(self, now: Optional[float] = None) -> bool:
        if self.expires_at is None:
            return False
        now = time.time() if now is None else now
        return now >= self.expires_at


class Store(Protocol):
    """Storage contract shared by every backend."""

    def next_id(self) -> int: ...
    def create_link(self, record: LinkRecord) -> LinkRecord: ...
    def get(self, code: str) -> Optional[LinkRecord]: ...
    def find_by_url(self, long_url: str) -> Optional[LinkRecord]: ...
    def record_click(self, code: str, ts: float, referrer, user_agent) -> None: ...
    def click_count(self, code: str) -> int: ...
    def clicks(self, code: str) -> list: ...


class InMemoryStore:
    """Dictionary-backed store. Not durable; ideal for tests and demos."""

    def __init__(self) -> None:
        self._links: dict[str, LinkRecord] = {}
        self._by_url: dict[str, str] = {}
        self._clicks: dict[str, list] = {}
        self._counter = 0

    def next_id(self) -> int:
        self._counter += 1
        return self._counter

    def create_link(self, record: LinkRecord) -> LinkRecord:
        if record.code in self._links:
            raise KeyError(f"code already exists: {record.code}")
        self._links[record.code] = record
        self._by_url.setdefault(record.long_url, record.code)
        self._clicks.setdefault(record.code, [])
        return record

    def get(self, code: str) -> Optional[LinkRecord]:
        return self._links.get(code)

    def find_by_url(self, long_url: str) -> Optional[LinkRecord]:
        code = self._by_url.get(long_url)
        return self._links.get(code) if code else None

    def record_click(self, code: str, ts: float, referrer, user_agent) -> None:
        self._clicks.setdefault(code, []).append(
            {"ts": ts, "referrer": referrer, "user_agent": user_agent}
        )

    def click_count(self, code: str) -> int:
        return len(self._clicks.get(code, []))

    def clicks(self, code: str) -> list:
        return list(self._clicks.get(code, []))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS id_seq (id INTEGER PRIMARY KEY AUTOINCREMENT);
CREATE TABLE IF NOT EXISTS links (
    code       TEXT PRIMARY KEY,
    long_url   TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL
);
CREATE INDEX IF NOT EXISTS idx_links_url ON links(long_url);
CREATE TABLE IF NOT EXISTS clicks (
    code       TEXT NOT NULL,
    ts         REAL NOT NULL,
    referrer   TEXT,
    user_agent TEXT
);
CREATE INDEX IF NOT EXISTS idx_clicks_code ON clicks(code);
"""


class SqliteStore:
    """Durable store backed by SQLite (standard library, no external service).

    ``autocommit`` controls whether every write (link creation, click
    recording) is flushed to disk immediately (the default, matching the
    original behaviour) or deferred and flushed in batches via
    :meth:`flush`/:meth:`set_autocommit`. Batching avoids paying an fsync-like
    commit cost on every single write, which is the dominant cost for
    write-heavy workloads (e.g. click recording) against SQLite; it is
    exposed as an explicit, reversible toggle so callers who need immediate
    durability keep it, while the performance toolkit can enable batching for
    measurable throughput gains.
    """

    def __init__(self, path: str = ":memory:", autocommit: bool = True) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self.autocommit = autocommit

    def set_autocommit(self, enabled: bool) -> None:
        """Enable/disable immediate commits. Turning it back on flushes pending writes."""
        self.autocommit = enabled
        if enabled:
            self.flush()

    def flush(self) -> None:
        """Force any pending, uncommitted writes to disk."""
        self._conn.commit()

    def _maybe_commit(self) -> None:
        if self.autocommit:
            self._conn.commit()

    def next_id(self) -> int:
        cur = self._conn.execute("INSERT INTO id_seq DEFAULT VALUES")
        self._maybe_commit()
        return int(cur.lastrowid)

    def create_link(self, record: LinkRecord) -> LinkRecord:
        self._conn.execute(
            "INSERT INTO links(code, long_url, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (record.code, record.long_url, record.created_at, record.expires_at),
        )
        self._maybe_commit()
        return record

    def get(self, code: str) -> Optional[LinkRecord]:
        row = self._conn.execute("SELECT * FROM links WHERE code = ?", (code,)).fetchone()
        return self._to_record(row) if row else None

    def find_by_url(self, long_url: str) -> Optional[LinkRecord]:
        row = self._conn.execute(
            "SELECT * FROM links WHERE long_url = ? ORDER BY created_at ASC LIMIT 1",
            (long_url,),
        ).fetchone()
        return self._to_record(row) if row else None

    def record_click(self, code: str, ts: float, referrer, user_agent) -> None:
        self._conn.execute(
            "INSERT INTO clicks(code, ts, referrer, user_agent) VALUES (?, ?, ?, ?)",
            (code, ts, referrer, user_agent),
        )
        self._maybe_commit()

    def click_count(self, code: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM clicks WHERE code = ?", (code,)
        ).fetchone()
        return int(row["n"])

    def clicks(self, code: str) -> list:
        rows = self._conn.execute(
            "SELECT ts, referrer, user_agent FROM clicks WHERE code = ?", (code,)
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _to_record(row: sqlite3.Row) -> LinkRecord:
        return LinkRecord(
            code=row["code"],
            long_url=row["long_url"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )
