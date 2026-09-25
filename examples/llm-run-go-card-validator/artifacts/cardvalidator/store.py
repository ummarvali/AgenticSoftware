"""In-memory velocity/idempotency store plus SQLite blacklist/audit persistence."""
import sqlite3
import threading
import time
from datetime import datetime, timezone


class Store:
    def __init__(self, db_path=":memory:"):
        self.lock = threading.Lock()
        self.velocity = {}
        self.idempotency = {}
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self):
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS blacklist "
            "(card_token TEXT PRIMARY KEY, reason TEXT, added_at TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS audit_log "
            "(transaction_id TEXT, bin TEXT, last4 TEXT, network TEXT, "
            "amount REAL, decision TEXT, reason_codes TEXT, timestamp TEXT)"
        )
        self.conn.commit()

    def add_blacklist(self, token, reason):
        added_at = datetime.now(timezone.utc).isoformat()
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO blacklist (card_token, reason, added_at) VALUES (?, ?, ?)",
                (token, reason, added_at),
            )
            self.conn.commit()
        return added_at

    def is_blacklisted(self, token):
        cur = self.conn.execute("SELECT 1 FROM blacklist WHERE card_token=?", (token,))
        return cur.fetchone() is not None

    def add_audit_log(self, entry):
        with self.lock:
            self.conn.execute(
                "INSERT INTO audit_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.get("transaction_id"),
                    entry.get("bin"),
                    entry.get("last4"),
                    entry.get("network"),
                    entry.get("amount"),
                    entry.get("decision"),
                    ",".join(entry.get("reason_codes", [])),
                    entry.get("timestamp"),
                ),
            )
            self.conn.commit()

    def record_velocity(self, token, ts, amount):
        with self.lock:
            self.velocity.setdefault(token, []).append((ts, amount))

    def get_velocity(self, token, window_seconds, now):
        with self.lock:
            records = self.velocity.get(token, [])
            cutoff = now - window_seconds
            records = [r for r in records if r[0] >= cutoff]
            self.velocity[token] = records
            return list(records)

    def sum_amount_since(self, token, since_ts, now):
        with self.lock:
            records = self.velocity.get(token, [])
            return sum(a for t, a in records if t >= since_ts)

    def get_idempotent(self, key):
        with self.lock:
            entry = self.idempotency.get(key)
            if entry and entry[1] > time.time():
                return entry[0]
            return None

    def set_idempotent(self, key, result, ttl=300):
        with self.lock:
            self.idempotency[key] = (result, time.time() + ttl)

    def is_ok(self):
        try:
            self.conn.execute("SELECT 1")
            return True
        except Exception:
            return False
