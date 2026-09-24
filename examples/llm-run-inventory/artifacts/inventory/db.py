"""SQLite persistence layer for the inventory service."""
import sqlite3
import datetime


def now():
    return datetime.datetime.utcnow().isoformat()


def get_conn(path=":memory:"):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS warehouses(
    id TEXT PRIMARY KEY, name TEXT, location TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS products(
    id TEXT PRIMARY KEY, name TEXT, unit TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS stock_levels(
    warehouse_id TEXT, product_id TEXT, quantity INTEGER NOT NULL DEFAULT 0,
    threshold INTEGER, version INTEGER NOT NULL DEFAULT 0, updated_at TEXT,
    PRIMARY KEY(warehouse_id, product_id)
);
CREATE TABLE IF NOT EXISTS global_settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS stock_adjustments(
    id TEXT PRIMARY KEY, warehouse_id TEXT, product_id TEXT, change_type TEXT,
    delta INTEGER, quantity_before INTEGER, quantity_after INTEGER,
    reason TEXT, actor TEXT, idempotency_key TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS alerts(
    id TEXT PRIMARY KEY, warehouse_id TEXT, product_id TEXT, threshold INTEGER,
    quantity_at_trigger INTEGER, status TEXT, created_at TEXT, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS idempotency_keys(
    key TEXT PRIMARY KEY, endpoint TEXT, request_hash TEXT,
    response_body TEXT, status_code INTEGER, created_at TEXT
);
CREATE TABLE IF NOT EXISTS api_keys(
    key TEXT PRIMARY KEY, owner TEXT, role TEXT, created_at TEXT
);
"""


def init_db(conn):
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO global_settings(key, value) VALUES('default_low_stock_threshold','10')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO api_keys(key, owner, role, created_at) VALUES('test-key','admin','admin',?)",
        (now(),),
    )
    conn.commit()
    return conn
