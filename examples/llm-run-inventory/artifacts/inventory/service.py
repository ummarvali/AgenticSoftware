"""Business logic: items, warehouses, stock, adjustments, audit."""
import hashlib
import json
import logging

from . import db
from . import alerts as alerts_mod

logger = logging.getLogger("inventory.service")


class InventoryService:
    def __init__(self, path):
        self.path = path

    def _conn(self):
        return db.get_conn(self.path)

    def verify_api_key(self, key):
        if not key:
            return False
        h = hashlib.sha256(key.encode()).hexdigest()
        conn = self._conn()
        row = conn.execute(
            "SELECT 1 FROM api_keys WHERE key_hash=? AND active=1", (h,)
        ).fetchone()
        conn.close()
        return row is not None

    # ---------------- items ----------------
    def create_item(self, sku, name, metadata=None):
        conn = self._conn()
        ts = db.now()
        try:
            cur = conn.execute(
                "INSERT INTO items(sku,name,metadata,created_at,updated_at) VALUES(?,?,?,?,?)",
                (sku, name, json.dumps(metadata or {}), ts, ts),
            )
            item_id = cur.lastrowid
        except Exception as e:
            raise ValueError(f"could not create item: {e}")
        finally:
            conn.close()
        return {"id": item_id, "sku": sku, "name": name, "metadata": metadata or {}, "created_at": ts}

    def get_item(self, item_id):
        conn = self._conn()
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        conn.close()
        if not row:
            raise LookupError("item not found")
        d = dict(row)
        d["metadata"] = json.loads(d["metadata"] or "{}")
        return d

    def list_items(self, page, page_size):
        conn = self._conn()
        total = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        offset = (page - 1) * page_size
        rows = conn.execute(
            "SELECT * FROM items ORDER BY id LIMIT ? OFFSET ?", (page_size, offset)
        ).fetchall()
        conn.close()
        items = []
        for r in rows:
            d = dict(r)
            d["metadata"] = json.loads(d["metadata"] or "{}")
            items.append(d)
        return items, total

    # ---------------- warehouses ----------------
    def create_warehouse(self, code, name, location=None):
        conn = self._conn()
        ts = db.now()
        try:
            cur = conn.execute(
                "INSERT INTO warehouses(code,name,location,created_at) VALUES(?,?,?,?)",
                (code, name, location, ts),
            )
            wid = cur.lastrowid
        except Exception as e:
            raise ValueError(f"could not create warehouse: {e}")
        finally:
            conn.close()
        return {"id": wid, "code": code, "name": name, "location": location, "created_at": ts}

    def list_warehouses(self, page, page_size):
        conn = self._conn()
        total = conn.execute("SELECT COUNT(*) c FROM warehouses").fetchone()["c"]
        offset = (page - 1) * page_size
        rows = conn.execute(
            "SELECT * FROM warehouses ORDER BY id LIMIT ? OFFSET ?", (page_size, offset)
        ).fetchall()
        result = []
        for w in rows:
            summary = conn.execute(
                "SELECT COALESCE(SUM(quantity),0) q, COUNT(*) c FROM stock WHERE warehouse_id=?",
                (w["id"],),
            ).fetchone()
            low = conn.execute(
                "SELECT COUNT(*) c FROM stock WHERE warehouse_id=? AND quantity <= "
                "COALESCE(low_stock_threshold, ?)",
                (w["id"], alerts_mod.DEFAULT_THRESHOLD),
            ).fetchone()["c"]
            result.append({
                "id": w["id"], "code": w["code"], "name": w["name"],
                "total_quantity": summary["q"], "item_count": summary["c"],
                "low_stock_count": low,
            })
        conn.close()
        return result, total

    # ---------------- stock ----------------
    def _get_or_create_stock_row(self, conn, item_id, warehouse_id):
        row = conn.execute(
            "SELECT * FROM stock WHERE item_id=? AND warehouse_id=?", (item_id, warehouse_id)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO stock(item_id,warehouse_id,quantity,updated_at) VALUES(?,?,0,?)",
                (item_id, warehouse_id, db.now()),
            )
            row = conn.execute(
                "SELECT * FROM stock WHERE item_id=? AND warehouse_id=?", (item_id, warehouse_id)
            ).fetchone()
        return row

    def adjust_stock(self, item_id, warehouse_id, delta, reason, actor, idempotency_key=None):
        conn = self._conn()
        try:
            if idempotency_key:
                existing = conn.execute(
                    "SELECT * FROM stock_adjustments WHERE idempotency_key=?", (idempotency_key,)
                ).fetchone()
                if existing:
                    row = conn.execute(
                        "SELECT * FROM stock WHERE item_id=? AND warehouse_id=?",
                        (item_id, warehouse_id),
                    ).fetchone()
                    threshold = row["low_stock_threshold"] if row else None
                    qty = row["quantity"] if row else existing["resulting_quantity"]
                    eff = threshold if threshold is not None else alerts_mod.DEFAULT_THRESHOLD
                    return {
                        "item_id": item_id, "warehouse_id": warehouse_id,
                        "quantity": qty, "delta_applied": existing["delta"],
                        "adjustment_id": existing["id"], "alert_triggered": qty <= eff,
                    }
            conn.execute("BEGIN IMMEDIATE")
            row = self._get_or_create_stock_row(conn, item_id, warehouse_id)
            new_qty = row["quantity"] + delta
            if new_qty < 0:
                conn.execute("ROLLBACK")
                raise ValueError("insufficient stock for adjustment")
            ts = db.now()
            conn.execute(
                "UPDATE stock SET quantity=?, updated_at=? WHERE item_id=? AND warehouse_id=?",
                (new_qty, ts, item_id, warehouse_id),
            )
            cur = conn.execute(
                "INSERT INTO stock_adjustments(item_id,warehouse_id,delta,reason,actor,"
                "idempotency_key,resulting_quantity,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (item_id, warehouse_id, delta, reason, actor, idempotency_key, new_qty, ts),
            )
            adj_id = cur.lastrowid
            threshold = row["low_stock_threshold"]
            triggered = alerts_mod.evaluate_and_alert(conn, item_id, warehouse_id, new_qty, threshold)
            conn.execute("COMMIT")
            logger.info(json.dumps({
                "event": "stock_adjusted", "item_id": item_id, "warehouse_id": warehouse_id,
                "delta": delta, "resulting_quantity": new_qty, "actor": actor,
            }))
            return {
                "item_id": item_id, "warehouse_id": warehouse_id, "quantity": new_qty,
                "delta_applied": delta, "adjustment_id": adj_id, "alert_triggered": triggered,
            }
        except ValueError:
            raise
        except Exception as e:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def add_stock(self, item_id, warehouse_id, quantity, actor, idempotency_key=None):
        if quantity <= 0:
            raise ValueError("quantity must be positive for add_stock")
        result = self.adjust_stock(
            item_id, warehouse_id, quantity, "restock", actor, idempotency_key
        )
        return {
            "item_id": item_id, "warehouse_id": warehouse_id,
            "quantity": result["quantity"], "updated_at": db.now(),
        }

    def get_stock(self, item_id, warehouse_id):
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM stock WHERE item_id=? AND warehouse_id=?", (item_id, warehouse_id)
        ).fetchone()
        conn.close()
        if not row:
            raise LookupError("stock record not found")
        return {
            "item_id": item_id, "warehouse_id": warehouse_id, "quantity": row["quantity"],
            "low_stock_threshold": row["low_stock_threshold"], "updated_at": row["updated_at"],
        }

    def get_stock_by_item(self, item_id):
        conn = self._conn()
        rows = conn.execute(
            "SELECT warehouse_id, quantity FROM stock WHERE item_id=?", (item_id,)
        ).fetchall()
        conn.close()
        total = sum(r["quantity"] for r in rows)
        return {
            "item_id": item_id, "total_quantity": total,
            "by_warehouse": [{"warehouse_id": r["warehouse_id"], "quantity": r["quantity"]} for r in rows],
        }

    def list_stock(self, page, page_size, item_id=None, warehouse_id=None):
        conn = self._conn()
        clauses, params = [], []
        if item_id is not None:
            clauses.append("item_id=?")
            params.append(item_id)
        if warehouse_id is not None:
            clauses.append("warehouse_id=?")
            params.append(warehouse_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        total = conn.execute(f"SELECT COUNT(*) c FROM stock {where}", params).fetchone()["c"]
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"SELECT * FROM stock {where} ORDER BY id LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows], total

    def set_threshold(self, item_id, warehouse_id, threshold):
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        self._get_or_create_stock_row(conn, item_id, warehouse_id)
        conn.execute(
            "UPDATE stock SET low_stock_threshold=? WHERE item_id=? AND warehouse_id=?",
            (threshold, item_id, warehouse_id),
        )
        conn.execute("COMMIT")
        conn.close()
        return {"item_id": item_id, "warehouse_id": warehouse_id, "low_stock_threshold": threshold}

    def list_audit(self, page, page_size, item_id=None):
        conn = self._conn()
        clause, params = "", []
        if item_id is not None:
            clause = "WHERE item_id=?"
            params.append(item_id)
        total = conn.execute(
            f"SELECT COUNT(*) c FROM stock_adjustments {clause}", params
        ).fetchone()["c"]
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"SELECT * FROM stock_adjustments {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows], total

    def list_alerts(self, page, page_size, low_stock_only=False):
        conn = self._conn()
        records, total = alerts_mod.list_alerts(conn, page, page_size, low_stock_only)
        conn.close()
        return records, total
