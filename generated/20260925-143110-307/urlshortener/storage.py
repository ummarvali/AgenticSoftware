"""SQLite-backed durable persistence for URLs, click events and API keys."""
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS urls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    short_code TEXT UNIQUE NOT NULL,
    long_url TEXT NOT NULL,
    long_url_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    is_custom_alias INTEGER DEFAULT 0,
    api_key_id INTEGER,
    click_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_urls_long_url_hash ON urls(long_url_hash);
CREATE UNIQUE INDEX IF NOT EXISTS idx_urls_short_code ON urls(short_code);

CREATE TABLE IF NOT EXISTS analytics_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    short_code TEXT NOT NULL,
    clicked_at TEXT NOT NULL,
    referrer TEXT,
    user_agent TEXT,
    ip_hash TEXT,
    FOREIGN KEY(short_code) REFERENCES urls(short_code)
);
CREATE INDEX IF NOT EXISTS idx_events_short_code_time ON analytics_events(short_code, clicked_at);

CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_value TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL,
    rate_limit_per_min INTEGER DEFAULT 60,
    is_active INTEGER DEFAULT 1
);
"""


class Storage:
    """Owns one SQLite connection per thread; serializes writes with a lock."""

    def __init__(self, db_path):
        self.db_path = db_path
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def _init_db(self):
        conn = self._get_conn()
        with self._lock:
            conn.executescript(SCHEMA)
            conn.commit()

    def insert_url(self, short_code, long_url, long_url_hash, created_at, expires_at,
                    is_custom_alias, api_key_id):
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute(
                "INSERT INTO urls (short_code, long_url, long_url_hash, created_at, "
                "expires_at, is_custom_alias, api_key_id) VALUES (?,?,?,?,?,?,?)",
                (short_code, long_url, long_url_hash, created_at, expires_at,
                 int(is_custom_alias), api_key_id),
            )
            conn.commit()
            return cur.lastrowid

    def find_active_by_hash(self, long_url_hash):
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT * FROM urls WHERE long_url_hash=? AND is_custom_alias=0 "
            "AND expires_at IS NULL ORDER BY id ASC LIMIT 1",
            (long_url_hash,),
        )
        return cur.fetchone()

    def get_by_short_code(self, short_code):
        conn = self._get_conn()
        cur = conn.execute("SELECT * FROM urls WHERE short_code=?", (short_code,))
        return cur.fetchone()

    def increment_click(self, short_code):
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "UPDATE urls SET click_count = click_count + 1 WHERE short_code=?",
                (short_code,),
            )
            conn.commit()

    def insert_event(self, short_code, clicked_at, referrer, user_agent, ip_hash):
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "INSERT INTO analytics_events (short_code, clicked_at, referrer, "
                "user_agent, ip_hash) VALUES (?,?,?,?,?)",
                (short_code, clicked_at, referrer, user_agent, ip_hash),
            )
            conn.commit()

    def get_recent_events(self, short_code, limit=20):
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT clicked_at, referrer, user_agent FROM analytics_events "
            "WHERE short_code=? ORDER BY clicked_at DESC LIMIT ?",
            (short_code, limit),
        )
        return cur.fetchall()

    def get_referrer_counts(self, short_code):
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT COALESCE(referrer, 'unknown') AS r, COUNT(*) AS c "
            "FROM analytics_events WHERE short_code=? GROUP BY r",
            (short_code,),
        )
        return {row["r"]: row["c"] for row in cur.fetchall()}

    def purge_expired(self, now_iso):
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute(
                "SELECT short_code FROM urls WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (now_iso,),
            )
            codes = [row["short_code"] for row in cur.fetchall()]
            if codes:
                conn.executemany(
                    "DELETE FROM analytics_events WHERE short_code=?", [(c,) for c in codes]
                )
                conn.executemany("DELETE FROM urls WHERE short_code=?", [(c,) for c in codes])
                conn.commit()
            return codes

    def create_api_key(self, key_value, created_at, rate_limit_per_min):
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "INSERT INTO api_keys (key_value, created_at, rate_limit_per_min, "
                "is_active) VALUES (?,?,?,1)",
                (key_value, created_at, rate_limit_per_min),
            )
            conn.commit()

    def get_api_key(self, key_value):
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT * FROM api_keys WHERE key_value=? AND is_active=1", (key_value,)
        )
        return cur.fetchone()

    def health_check(self):
        try:
            conn = self._get_conn()
            conn.execute("SELECT 1")
            return True
        except sqlite3.Error:
            return False
