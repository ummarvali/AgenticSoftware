"""SQLite-backed audit log and durable blacklist store.

Only anonymized/masked data is ever written here; raw PAN/CVV never reach
this layer.
"""

import json
import sqlite3
import threading


class Storage:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """CREATE TABLE IF NOT EXISTS audit (
                    id TEXT PRIMARY KEY,
                    request_id TEXT,
                    timestamp TEXT,
                    masked_pan TEXT,
                    bin TEXT,
                    last4 TEXT,
                    network TEXT,
                    amount REAL,
                    currency TEXT,
                    merchant_id TEXT,
                    valid INTEGER,
                    reason_codes TEXT,
                    risk_score REAL
                )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS blacklist (
                    hashed_pan TEXT PRIMARY KEY,
                    reason TEXT,
                    added_at TEXT
                )"""
            )
            self._conn.commit()

    def add_audit_record(self, record: dict):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO audit VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record["id"],
                    record["request_id"],
                    record["timestamp"],
                    record["masked_pan"],
                    record["bin"],
                    record["last4"],
                    record["network"],
                    record["amount"],
                    record["currency"],
                    record["merchant_id"],
                    int(record["valid"]),
                    json.dumps(record["reason_codes"]),
                    record["risk_score"],
                ),
            )
            self._conn.commit()

    def get_audit_record(self, request_id: str):
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, request_id, timestamp, masked_pan, bin, last4, network, amount, "
                "currency, merchant_id, valid, reason_codes, risk_score FROM audit "
                "WHERE request_id=?",
                (request_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        cols = [
            "id", "request_id", "timestamp", "masked_pan", "bin", "last4", "network",
            "amount", "currency", "merchant_id", "valid", "reason_codes", "risk_score",
        ]
        record = dict(zip(cols, row))
        record["reason_codes"] = json.loads(record["reason_codes"])
        record["valid"] = bool(record["valid"])
        return record

    def add_blacklist(self, hashed_pan: str, reason: str, added_at: str):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO blacklist VALUES (?,?,?)",
                (hashed_pan, reason, added_at),
            )
            self._conn.commit()

    def is_blacklisted(self, hashed_pan: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM blacklist WHERE hashed_pan=?", (hashed_pan,)
            )
            return cur.fetchone() is not None

    def check_connectivity(self) -> bool:
        try:
            with self._lock:
                self._conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    def close(self):
        self._conn.close()
