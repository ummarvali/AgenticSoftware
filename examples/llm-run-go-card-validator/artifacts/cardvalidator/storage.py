"""SQLite-backed idempotency cache, velocity ledger, blacklist and audit log."""
import sqlite3
import threading
import json
import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS idempotency_cache(
    key TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS velocity_ledger(
    card_token_hash TEXT NOT NULL,
    period_key TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    tx_count INTEGER NOT NULL,
    PRIMARY KEY(card_token_hash, period_key)
);
CREATE TABLE IF NOT EXISTS blacklist(
    card_token_hash TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    card_token_hash TEXT,
    masked_pan TEXT,
    outcome TEXT NOT NULL,
    reasons TEXT,
    amount_cents INTEGER,
    currency TEXT,
    created_at TEXT NOT NULL
);
"""


def _utcnow_naive():
    """Return a timezone-naive UTC datetime without using the deprecated utcnow()."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class Storage:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def get_idempotent(self, key, ttl_seconds):
        with self._lock:
            cur = self._conn.execute(
                "SELECT response_json, status_code, created_at FROM idempotency_cache WHERE key=?", (key,))
            row = cur.fetchone()
        if not row:
            return None
        response_json, status_code, created_at = row
        created = datetime.datetime.fromisoformat(created_at)
        if (_utcnow_naive() - created).total_seconds() > ttl_seconds:
            return None
        return response_json, status_code

    def put_idempotent(self, key, response_json, status_code):
        now = _utcnow_naive().isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO idempotency_cache(key, response_json, status_code, created_at) "
                "VALUES (?,?,?,?)", (key, response_json, status_code, now))
            self._conn.commit()

    def get_velocity(self, card_token_hash, period_key):
        with self._lock:
            cur = self._conn.execute(
                "SELECT amount_cents, tx_count FROM velocity_ledger WHERE card_token_hash=? AND period_key=?",
                (card_token_hash, period_key))
            row = cur.fetchone()
        if not row:
            return 0, 0
        return row[0], row[1]

    def add_velocity(self, card_token_hash, period_key, amount_cents):
        with self._lock:
            cur = self._conn.execute(
                "SELECT amount_cents, tx_count FROM velocity_ledger WHERE card_token_hash=? AND period_key=?",
                (card_token_hash, period_key))
            row = cur.fetchone()
            if row:
                self._conn.execute(
                    "UPDATE velocity_ledger SET amount_cents=?, tx_count=? WHERE card_token_hash=? AND period_key=?",
                    (row[0] + amount_cents, row[1] + 1, card_token_hash, period_key))
            else:
                self._conn.execute(
                    "INSERT INTO velocity_ledger(card_token_hash, period_key, amount_cents, tx_count) "
                    "VALUES (?,?,?,1)", (card_token_hash, period_key, amount_cents))
            self._conn.commit()

    def is_blacklisted(self, card_token_hash):
        with self._lock:
            cur = self._conn.execute(
                "SELECT reason FROM blacklist WHERE card_token_hash=?", (card_token_hash,))
            row = cur.fetchone()
        return row[0] if row else None

    def add_blacklist(self, card_token_hash, reason):
        now = _utcnow_naive().isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO blacklist(card_token_hash, reason, added_at) VALUES (?,?,?)",
                (card_token_hash, reason, now))
            self._conn.commit()

    def write_audit(self, request_id, card_token_hash, masked_pan, outcome, reasons, amount_cents, currency):
        now = _utcnow_naive().isoformat()
        reasons_str = ",".join(reasons)
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_log(request_id, card_token_hash, masked_pan, outcome, reasons, "
                "amount_cents, currency, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (request_id, card_token_hash, masked_pan, outcome, reasons_str, amount_cents, currency, now))
            self._conn.commit()
        entry = {
            "request_id": request_id, "card_token_hash": card_token_hash, "masked_pan": masked_pan,
            "outcome": outcome, "reasons": reasons, "amount_cents": amount_cents,
            "currency": currency, "created_at": now,
        }
        print(json.dumps(entry))

    def check_writable(self):
        try:
            with self._lock:
                self._conn.execute("SELECT 1")
            return True
        except Exception:
            return False
