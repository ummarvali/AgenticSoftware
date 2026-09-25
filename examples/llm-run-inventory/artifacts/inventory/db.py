"""SQLite persistence layer: schema creation, connection and seed data.

A single shared connection is used (WAL mode) guarded by a re-entrant
lock so that writes are serialized while reads stay fast and durable.
"""
import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS warehouses (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    sku TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_items (
    warehouse_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity >= 0),
    version INTEGER NOT NULL DEFAULT 1,
    threshold INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (warehouse_id, product_id),
    FOREIGN KEY (warehouse_id) REFERENCES warehouses(id),
    FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE TABLE IF NOT EXISTS global_threshold (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    default_threshold INTEGER
);

CREATE TABLE IF NOT EXISTS stock_adjustments (
    id TEXT PRIMARY KEY,
    warehouse_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    delta INTEGER NOT NULL,
    resulting_quantity INTEGER NOT NULL,
    reason TEXT,
    actor TEXT,
    created_at TEXT NOT NULL,
    idempotency_key TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    warehouse_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    threshold INTEGER NOT NULL,
    quantity_at_trigger INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','RESOLVED')),
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    response_body TEXT NOT NULL,
    response_status INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    key TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK(role IN ('READ','WRITE','ADMIN')),
    owner TEXT,
    created_at TEXT NOT NULL
);
"""

DEFAULT_API_KEYS = [
    ("demo-read-key", "READ", "reader"),
    ("demo-write-key", "WRITE", "writer"),
    ("demo-admin-key", "ADMIN", "admin"),
]


def _now():
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Owns the single SQLite connection and the write/read lock."""

    def __init__(self, path=":memory:"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.lock = threading.RLock()
        self._init_schema()
        self._seed()

    def _init_schema(self):
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def _seed(self):
        with self.lock:
            row = self.conn.execute("SELECT id FROM global_threshold WHERE id=1").fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO global_threshold(id, default_threshold) VALUES (1, NULL)"
                )
            existing = self.conn.execute("SELECT COUNT(*) AS c FROM api_keys").fetchone()
            if existing["c"] == 0:
                now = _now()
                for key, role, owner in DEFAULT_API_KEYS:
                    self.conn.execute(
                        "INSERT INTO api_keys(key, role, owner, created_at) VALUES (?,?,?,?)",
                        (key, role, owner, now),
                    )
            self.conn.commit()
