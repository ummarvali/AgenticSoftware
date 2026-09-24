"""SQLite persistence layer for the URL shortener (WAL mode, thread-safe)."""
import sqlite3
import threading

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS url_mappings (
    short_code TEXT PRIMARY KEY,
    long_url TEXT NOT NULL,
    owner_id TEXT,
    custom_alias INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    is_active INTEGER DEFAULT 1,
    click_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
    key_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS click_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    short_code TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    referrer TEXT,
    country_code TEXT,
    device_type TEXT,
    browser TEXT,
    ip_hash TEXT
);
CREATE TABLE IF NOT EXISTS counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
"""


class Database:
    """Thin wrapper around a shared SQLite connection."""

    def __init__(self, path=":memory:"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL;")
        except sqlite3.OperationalError:
            pass
        with _lock:
            self.conn.executescript(SCHEMA)
            self.conn.execute(
                "INSERT OR IGNORE INTO counters(name, value) VALUES ('short_code', 1000)"
            )
            self.conn.commit()

    def execute(self, sql, params=()):
        with _lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def query(self, sql, params=()):
        with _lock:
            cur = self.conn.execute(sql, params)
            return cur.fetchall()

    def next_counter(self, name="short_code"):
        with _lock:
            row = self.conn.execute(
                "SELECT value FROM counters WHERE name=?", (name,)
            ).fetchone()
            val = row["value"] if row else 1000
            self.conn.execute(
                "INSERT OR IGNORE INTO counters(name, value) VALUES (?, ?)",
                (name, val),
            )
            self.conn.execute(
                "UPDATE counters SET value=? WHERE name=?", (val + 1, name)
            )
            self.conn.commit()
            return val
