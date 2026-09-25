"""SQLite-backed persistence for urls, clicks, aggregates and api keys."""
import sqlite3
import threading
from collections import Counter


class Storage:
    def __init__(self, path=":memory:"):
        self.path = path
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            c = self.conn.cursor()
            c.execute("""CREATE TABLE IF NOT EXISTS urls(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                short_code TEXT UNIQUE NOT NULL,
                long_url TEXT NOT NULL,
                owner_key TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                is_custom INTEGER DEFAULT 0,
                click_count INTEGER DEFAULT 0
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS clicks(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                short_code TEXT NOT NULL,
                clicked_at TEXT NOT NULL,
                referrer TEXT,
                user_agent TEXT,
                device TEXT,
                geo_country TEXT
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS daily_aggregates(
                short_code TEXT NOT NULL,
                day TEXT NOT NULL,
                clicks INTEGER NOT NULL,
                PRIMARY KEY(short_code, day)
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS api_keys(
                key TEXT PRIMARY KEY,
                owner_label TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            self.conn.commit()

    def insert_url(self, short_code, long_url, owner_key, created_at, expires_at, is_custom):
        with self._lock:
            try:
                self.conn.execute(
                    "INSERT INTO urls(short_code, long_url, owner_key, created_at, expires_at, is_custom) "
                    "VALUES (?,?,?,?,?,?)",
                    (short_code, long_url, owner_key, created_at, expires_at, int(is_custom)),
                )
                self.conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def get_url(self, short_code):
        with self._lock:
            cur = self.conn.execute("SELECT * FROM urls WHERE short_code=?", (short_code,))
            return cur.fetchone()

    def delete_url(self, short_code):
        with self._lock:
            cur = self.conn.execute("DELETE FROM urls WHERE short_code=?", (short_code,))
            self.conn.commit()
            return cur.rowcount > 0

    def next_id(self):
        with self._lock:
            cur = self.conn.execute("SELECT COUNT(*) as c FROM urls")
            return cur.fetchone()["c"] + 1

    def insert_clicks_batch(self, events):
        if not events:
            return
        with self._lock:
            self.conn.executemany(
                "INSERT INTO clicks(short_code, clicked_at, referrer, user_agent, device, geo_country) "
                "VALUES (?,?,?,?,?,?)",
                events,
            )
            counts = Counter(e[0] for e in events)
            for code, cnt in counts.items():
                self.conn.execute(
                    "UPDATE urls SET click_count = click_count + ? WHERE short_code=?", (cnt, code)
                )
            self.conn.commit()

    def upsert_daily_aggregate(self, short_code, day, count):
        with self._lock:
            self.conn.execute(
                """INSERT INTO daily_aggregates(short_code, day, clicks) VALUES (?,?,?)
                ON CONFLICT(short_code, day) DO UPDATE SET clicks = clicks + excluded.clicks""",
                (short_code, day, count),
            )
            self.conn.commit()

    def get_daily_aggregates(self, short_code):
        with self._lock:
            cur = self.conn.execute(
                "SELECT day, clicks FROM daily_aggregates WHERE short_code=? ORDER BY day", (short_code,)
            )
            return [dict(r) for r in cur.fetchall()]

    def get_recent_clicks(self, short_code, limit=10):
        with self._lock:
            cur = self.conn.execute(
                "SELECT clicked_at, referrer, device, geo_country FROM clicks "
                "WHERE short_code=? ORDER BY id DESC LIMIT ?",
                (short_code, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_api_key_owner(self, key):
        with self._lock:
            cur = self.conn.execute("SELECT owner_label FROM api_keys WHERE key=?", (key,))
            row = cur.fetchone()
            return row["owner_label"] if row else None

    def add_api_key(self, key, owner_label, created_at):
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO api_keys(key, owner_label, created_at) VALUES (?,?,?)",
                (key, owner_label, created_at),
            )
            self.conn.commit()
