"""SQLite persistence layer: schema + connection helper."""
import sqlite3
import hashlib
import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sku TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  metadata TEXT,
  created_at TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS warehouses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  location TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS stock (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL REFERENCES items(id),
  warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
  quantity INTEGER NOT NULL DEFAULT 0,
  low_stock_threshold INTEGER,
  updated_at TEXT,
  UNIQUE(item_id, warehouse_id)
);
CREATE TABLE IF NOT EXISTS stock_adjustments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL,
  warehouse_id INTEGER NOT NULL,
  delta INTEGER NOT NULL,
  reason TEXT,
  actor TEXT,
  idempotency_key TEXT UNIQUE,
  resulting_quantity INTEGER,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL,
  warehouse_id INTEGER NOT NULL,
  quantity INTEGER,
  threshold INTEGER,
  status TEXT CHECK(status in ('OPEN','ACKNOWLEDGED')),
  triggered_at TEXT,
  delivered INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS api_keys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key_hash TEXT UNIQUE,
  description TEXT,
  active INTEGER DEFAULT 1
);
"""

DEFAULT_API_KEY = "secret-key"


def get_conn(path):
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(path):
    conn = get_conn(path)
    conn.executescript(SCHEMA)
    key_hash = hashlib.sha256(DEFAULT_API_KEY.encode()).hexdigest()
    conn.execute(
        "INSERT OR IGNORE INTO api_keys(key_hash, description, active) VALUES (?,?,1)",
        (key_hash, "default"),
    )
    conn.close()


def now():
    return datetime.datetime.utcnow().isoformat()
