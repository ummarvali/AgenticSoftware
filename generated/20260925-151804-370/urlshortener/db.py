"""SQLite persistence layer: schema management, short-code generation and
a small in-process LRU cache used to speed up redirect lookups."""
import sqlite3
import threading
from collections import OrderedDict
from datetime import datetime, timezone

BASE62_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

SCHEMA = """
CREATE TABLE IF NOT EXISTS url_mappings (
    short_code TEXT PRIMARY KEY,
    long_url TEXT NOT NULL,
    owner_api_key TEXT,
    created_at TEXT,
    expires_at TEXT NULL,
    is_active INTEGER DEFAULT 1,
    is_custom_alias INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS api_keys (
    api_key TEXT PRIMARY KEY,
    owner_name TEXT,
    created_at TEXT,
    is_active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS click_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    short_code TEXT,
    clicked_at TEXT,
    referrer TEXT,
    user_agent TEXT,
    device TEXT,
    browser TEXT,
    geo_country TEXT NULL
);
CREATE TABLE IF NOT EXISTS short_code_counter (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    next_value INTEGER NOT NULL
);
"""


def utcnow_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def encode_base62(num):
    if num == 0:
        return BASE62_ALPHABET[0]
    base = len(BASE62_ALPHABET)
    digits = []
    while num > 0:
        num, rem = divmod(num, base)
        digits.append(BASE62_ALPHABET[rem])
    return "".join(reversed(digits))


class LRUCache:
    """Simple thread-safe process-local LRU cache."""

    def __init__(self, capacity):
        self.capacity = capacity
        self._data = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key not in self._data:
                return None
            self._data.move_to_end(key)
            return self._data[key]

    def set(self, key, value):
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            if len(self._data) > self.capacity:
                self._data.popitem(last=False)

    def invalidate(self, key):
        with self._lock:
            self._data.pop(key, None)


class Database:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute(
                "INSERT OR IGNORE INTO short_code_counter (id, next_value) VALUES (1, 1)"
            )
            self._conn.commit()

    def seed_api_keys(self, spec):
        if not spec:
            return
        now = utcnow_iso()
        with self._lock:
            for item in spec.split(","):
                item = item.strip()
                if not item:
                    continue
                if ":" in item:
                    key, owner = item.split(":", 1)
                else:
                    key, owner = item, "default"
                self._conn.execute(
                    "INSERT OR IGNORE INTO api_keys (api_key, owner_name, created_at, is_active) "
                    "VALUES (?, ?, ?, 1)",
                    (key.strip(), owner.strip(), now),
                )
            self._conn.commit()

    def verify_api_key(self, key):
        if not key:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT owner_name, is_active FROM api_keys WHERE api_key = ?", (key,)
            ).fetchone()
        if row and row["is_active"]:
            return row["owner_name"]
        return None

    def next_short_code(self):
        with self._lock:
            row = self._conn.execute(
                "SELECT next_value FROM short_code_counter WHERE id = 1"
            ).fetchone()
            value = row["next_value"]
            self._conn.execute(
                "UPDATE short_code_counter SET next_value = ? WHERE id = 1", (value + 1,)
            )
            self._conn.commit()
            return encode_base62(value)

    def create_mapping(self, short_code, long_url, owner_api_key, created_at, expires_at, is_custom):
        with self._lock:
            self._conn.execute(
                "INSERT INTO url_mappings "
                "(short_code, long_url, owner_api_key, created_at, expires_at, is_active, is_custom_alias) "
                "VALUES (?, ?, ?, ?, ?, 1, ?)",
                (short_code, long_url, owner_api_key, created_at, expires_at, 1 if is_custom else 0),
            )
            self._conn.commit()

    def get_mapping(self, short_code):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM url_mappings WHERE short_code = ?", (short_code,)
            ).fetchone()
        return dict(row) if row else None

    def deactivate_mapping(self, short_code):
        with self._lock:
            self._conn.execute(
                "UPDATE url_mappings SET is_active = 0 WHERE short_code = ?", (short_code,)
            )
            self._conn.commit()

    def record_click(self, short_code, clicked_at, referrer, user_agent, device, browser):
        with self._lock:
            self._conn.execute(
                "INSERT INTO click_events (short_code, clicked_at, referrer, user_agent, device, browser) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (short_code, clicked_at, referrer, user_agent, device, browser),
            )
            self._conn.commit()

    def total_clicks(self, short_code):
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) c FROM click_events WHERE short_code = ?", (short_code,)
            ).fetchone()
        return row["c"]

    def clicks_by_day(self, short_code):
        with self._lock:
            rows = self._conn.execute(
                "SELECT substr(clicked_at, 1, 10) d, COUNT(*) c FROM click_events "
                "WHERE short_code = ? GROUP BY d ORDER BY d",
                (short_code,),
            ).fetchall()
        return [{"date": r["d"], "count": r["c"]} for r in rows]

    def top_referrers(self, short_code, limit=5):
        with self._lock:
            rows = self._conn.execute(
                "SELECT COALESCE(NULLIF(referrer, ''), 'direct') r, COUNT(*) c FROM click_events "
                "WHERE short_code = ? GROUP BY r ORDER BY c DESC LIMIT ?",
                (short_code, limit),
            ).fetchall()
        return [{"referrer": r["r"], "count": r["c"]} for r in rows]

    def top_user_agents(self, short_code, limit=5):
        with self._lock:
            rows = self._conn.execute(
                "SELECT COALESCE(NULLIF(user_agent, ''), 'unknown') a, COUNT(*) c FROM click_events "
                "WHERE short_code = ? GROUP BY a ORDER BY c DESC LIMIT ?",
                (short_code, limit),
            ).fetchall()
        return [{"agent": r["a"], "count": r["c"]} for r in rows]

    def health_check(self):
        try:
            with self._lock:
                self._conn.execute("SELECT 1")
            return True
        except Exception:
            return False
