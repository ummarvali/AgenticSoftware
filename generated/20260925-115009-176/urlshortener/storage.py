"""Persistence layer: SQLite-backed storage for URLs, users, click events
and the blacklist. A single shared connection guarded by a lock is used so
the module is safe to call from multiple request-handling threads while
keeping the implementation simple (durability over raw throughput).
"""

import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS urls (
    short_code TEXT PRIMARY KEY,
    long_url TEXT NOT NULL,
    owner_id TEXT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NULL,
    max_clicks INTEGER NULL,
    click_count INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    is_custom_alias INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    api_token TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS click_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    short_code TEXT NOT NULL,
    ts TEXT NOT NULL,
    referrer TEXT NULL,
    user_agent TEXT NULL,
    ip_country TEXT NULL,
    device_type TEXT NULL,
    FOREIGN KEY(short_code) REFERENCES urls(short_code)
);

CREATE TABLE IF NOT EXISTS blacklist (
    pattern TEXT PRIMARY KEY
);
"""

DEFAULT_BLACKLIST = ["malware.test", "phishing.test", "badsite.test"]


class Storage:
    """Thread-safe SQLite storage facade."""

    def __init__(self, db_path):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()
            cur = self._conn.execute("SELECT COUNT(*) AS c FROM blacklist")
            if cur.fetchone()["c"] == 0:
                self._conn.executemany(
                    "INSERT INTO blacklist(pattern) VALUES (?)",
                    [(p,) for p in DEFAULT_BLACKLIST],
                )
                self._conn.commit()

    # -- urls -----------------------------------------------------------
    def insert_url(self, short_code, long_url, owner_id, created_at,
                    expires_at, is_custom_alias):
        with self._lock:
            self._conn.execute(
                "INSERT INTO urls(short_code, long_url, owner_id, created_at,"
                " expires_at, click_count, is_active, is_custom_alias)"
                " VALUES (?, ?, ?, ?, ?, 0, 1, ?)",
                (short_code, long_url, owner_id, created_at, expires_at,
                 1 if is_custom_alias else 0),
            )
            self._conn.commit()

    def get_url(self, short_code):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM urls WHERE short_code = ?", (short_code,)
            ).fetchone()
            return dict(row) if row else None

    def set_active(self, short_code, active):
        with self._lock:
            self._conn.execute(
                "UPDATE urls SET is_active = ? WHERE short_code = ?",
                (1 if active else 0, short_code),
            )
            self._conn.commit()

    def increment_click(self, short_code):
        with self._lock:
            self._conn.execute(
                "UPDATE urls SET click_count = click_count + 1"
                " WHERE short_code = ?",
                (short_code,),
            )
            self._conn.commit()

    # -- users ------------------------------------------------------------
    def insert_user(self, user_id, api_token, created_at):
        with self._lock:
            self._conn.execute(
                "INSERT INTO users(user_id, api_token, created_at)"
                " VALUES (?, ?, ?)",
                (user_id, api_token, created_at),
            )
            self._conn.commit()

    def get_user_by_token(self, api_token):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE api_token = ?", (api_token,)
            ).fetchone()
            return dict(row) if row else None

    # -- click events -----------------------------------------------------
    def insert_click_event(self, short_code, ts, referrer, user_agent,
                            ip_country, device_type):
        with self._lock:
            self._conn.execute(
                "INSERT INTO click_events(short_code, ts, referrer,"
                " user_agent, ip_country, device_type)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (short_code, ts, referrer, user_agent, ip_country,
                 device_type),
            )
            self._conn.commit()

    def get_click_events(self, short_code):
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM click_events WHERE short_code = ?"
                " ORDER BY ts ASC",
                (short_code,),
            ).fetchall()
            return [dict(r) for r in rows]

    # -- blacklist ----------------------------------------------------------
    def get_blacklist_patterns(self):
        with self._lock:
            rows = self._conn.execute("SELECT pattern FROM blacklist").fetchall()
            return [r["pattern"] for r in rows]

