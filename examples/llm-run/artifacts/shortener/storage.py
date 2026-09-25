"""SQLite persistence layer and a simple in-memory TTL cache.

Provides a thread-safe wrapper (`Database`) around a single SQLite connection
(sufficient for this single-process runnable slice) and `TTLCache`, a bounded
in-memory cache used to reduce database reads on the hot redirect path.
"""
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS urls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    short_code TEXT UNIQUE NOT NULL,
    long_url TEXT NOT NULL,
    owner_key TEXT,
    custom_alias INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS api_keys (
    key TEXT PRIMARY KEY,
    owner_name TEXT,
    created_at TEXT NOT NULL,
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS click_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    short_code TEXT NOT NULL,
    ts TEXT NOT NULL,
    referrer TEXT,
    user_agent TEXT,
    ip TEXT,
    geo_country TEXT
);
CREATE TABLE IF NOT EXISTS url_stats (
    short_code TEXT PRIMARY KEY,
    total_clicks INTEGER DEFAULT 0,
    last_click_at TEXT
);
"""


class Database:
    """Thread-safe wrapper around a single SQLite connection.

    All access is serialized with a lock; each write auto-commits so data is
    durable across process restarts.
    """

    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def execute(self, sql, params=()):
        with self.lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def query(self, sql, params=()):
        with self.lock:
            cur = self.conn.execute(sql, params)
            return cur.fetchall()

    def query_one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None


class TTLCache:
    """Bounded in-memory cache with a per-entry time-to-live.

    Used to cache short_code -> long_url lookups so hot redirects avoid a
    database round trip.
    """

    def __init__(self, maxsize=10000, ttl=60):
        self.maxsize = maxsize
        self.ttl = ttl
        self._data = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            value, expiry = item
            if expiry < time.time():
                del self._data[key]
                return None
            return value

    def set(self, key, value):
        with self._lock:
            if len(self._data) >= self.maxsize:
                oldest_key = next(iter(self._data))
                del self._data[oldest_key]
            self._data[key] = (value, time.time() + self.ttl)

    def invalidate(self, key):
        with self._lock:
            self._data.pop(key, None)
