"""Alert evaluation, storage and pluggable notifier."""
import json
import logging

from . import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("inventory.alerts")

DEFAULT_THRESHOLD = 10


def notify(alert):
    """Console/log stub simulating a queue publish (Kafka/SNS)."""
    logger.info(json.dumps({"event": "low_stock_alert", "alert": alert}))


def evaluate_and_alert(conn, item_id, warehouse_id, quantity, threshold):
    """Create an alert record if quantity <= threshold; returns bool triggered."""
    effective = threshold if threshold is not None else DEFAULT_THRESHOLD
    triggered = quantity <= effective
    if triggered:
        ts = db.now()
        cur = conn.execute(
            "INSERT INTO alerts(item_id, warehouse_id, quantity, threshold, status, "
            "triggered_at, delivered) VALUES (?,?,?,?,?,?,1)",
            (item_id, warehouse_id, quantity, effective, "OPEN", ts),
        )
        alert = {
            "id": cur.lastrowid,
            "item_id": item_id,
            "warehouse_id": warehouse_id,
            "quantity": quantity,
            "threshold": effective,
            "triggered_at": ts,
        }
        notify(alert)
    return triggered


def list_alerts(conn, page, page_size, low_stock_only=False):
    offset = (page - 1) * page_size
    where = "WHERE status='OPEN'" if low_stock_only else ""
    total = conn.execute(f"SELECT COUNT(*) c FROM alerts {where}").fetchone()["c"]
    rows = conn.execute(
        f"SELECT * FROM alerts {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        (page_size, offset),
    ).fetchall()
    records = [dict(r) for r in rows]
    return records, total
