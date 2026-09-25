"""Core business logic: catalog CRUD, stock intake/adjustment with
optimistic concurrency, threshold configuration, alert evaluation and
audit history. All persistence goes through the shared Database lock.
"""
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("inventory")


class NotFoundError(Exception):
    pass


class ValidationError(Exception):
    pass


class ConflictError(Exception):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _new_id():
    return uuid.uuid4().hex


class InventoryService:
    def __init__(self, db, event_bus, metrics):
        self.db = db
        self.event_bus = event_bus
        self.metrics = metrics

    # ---------------------------------------------------------- warehouses
    def create_warehouse(self, name, location):
        if not name:
            raise ValidationError("name is required")
        wid, now = _new_id(), _now()
        with self.db.lock:
            self.db.conn.execute(
                "INSERT INTO warehouses(id,name,location,created_at) VALUES (?,?,?,?)",
                (wid, name, location, now),
            )
            self.db.conn.commit()
        return {"id": wid, "name": name, "location": location, "created_at": now}

    def get_warehouse(self, warehouse_id):
        with self.db.lock:
            row = self.db.conn.execute(
                "SELECT * FROM warehouses WHERE id=?", (warehouse_id,)
            ).fetchone()
        if not row:
            raise NotFoundError(f"warehouse {warehouse_id} not found")
        return dict(row)

    def list_warehouses(self):
        with self.db.lock:
            rows = self.db.conn.execute("SELECT id,name,location FROM warehouses").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ products
    def create_product(self, sku, name, description):
        if not sku or not name:
            raise ValidationError("sku and name are required")
        pid, now = _new_id(), _now()
        with self.db.lock:
            try:
                self.db.conn.execute(
                    "INSERT INTO products(id,sku,name,description,created_at) VALUES (?,?,?,?,?)",
                    (pid, sku, name, description, now),
                )
                self.db.conn.commit()
            except sqlite3.IntegrityError:
                raise ConflictError(f"sku {sku} already exists")
        return {"id": pid, "sku": sku, "name": name, "description": description}

    def get_product(self, product_id):
        with self.db.lock:
            row = self.db.conn.execute(
                "SELECT * FROM products WHERE id=?", (product_id,)
            ).fetchone()
        if not row:
            raise NotFoundError(f"product {product_id} not found")
        return dict(row)

    def list_products(self):
        with self.db.lock:
            rows = self.db.conn.execute("SELECT id,sku,name FROM products").fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- misc
    def _ensure_warehouse_product(self, warehouse_id, product_id):
        if not self.db.conn.execute(
            "SELECT 1 FROM warehouses WHERE id=?", (warehouse_id,)
        ).fetchone():
            raise NotFoundError(f"warehouse {warehouse_id} not found")
        if not self.db.conn.execute(
            "SELECT 1 FROM products WHERE id=?", (product_id,)
        ).fetchone():
            raise NotFoundError(f"product {product_id} not found")

    def _get_stock_row(self, warehouse_id, product_id):
        return self.db.conn.execute(
            "SELECT * FROM stock_items WHERE warehouse_id=? AND product_id=?",
            (warehouse_id, product_id),
        ).fetchone()

    def _effective_threshold(self, item_threshold):
        if item_threshold is not None:
            return item_threshold
        row = self.db.conn.execute(
            "SELECT default_threshold FROM global_threshold WHERE id=1"
        ).fetchone()
        return row["default_threshold"] if row else None

    # ------------------------------------------------------------- stock
    def add_stock(self, warehouse_id, product_id, quantity, threshold=None, actor="system", reason=None):
        if not warehouse_id or not product_id:
            raise ValidationError("warehouse_id and product_id are required")
        if not isinstance(quantity, int) or quantity <= 0:
            raise ValidationError("quantity must be a positive integer")
        if threshold is not None and (not isinstance(threshold, int) or threshold < 0):
            raise ValidationError("threshold must be a non-negative integer")
        now = _now()
        with self.db.lock:
            self._ensure_warehouse_product(warehouse_id, product_id)
            row = self._get_stock_row(warehouse_id, product_id)
            if row is None:
                new_qty, new_version, stored_threshold = quantity, 1, threshold
                self.db.conn.execute(
                    "INSERT INTO stock_items(warehouse_id,product_id,quantity,version,threshold,updated_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (warehouse_id, product_id, new_qty, new_version, stored_threshold, now),
                )
            else:
                new_qty = row["quantity"] + quantity
                new_version = row["version"] + 1
                stored_threshold = threshold if threshold is not None else row["threshold"]
                self.db.conn.execute(
                    "UPDATE stock_items SET quantity=?, version=?, threshold=?, updated_at=?"
                    " WHERE warehouse_id=? AND product_id=?",
                    (new_qty, new_version, stored_threshold, now, warehouse_id, product_id),
                )
            self.db.conn.execute(
                "INSERT INTO stock_adjustments(id,warehouse_id,product_id,delta,resulting_quantity,"
                "reason,actor,created_at,idempotency_key) VALUES (?,?,?,?,?,?,?,?,NULL)",
                (_new_id(), warehouse_id, product_id, quantity, new_qty, reason or "STOCK_ADD", actor, now),
            )
            eff_threshold = self._effective_threshold(stored_threshold)
            self._evaluate_alert(warehouse_id, product_id, new_qty, eff_threshold)
            self.db.conn.commit()
            logger.info("stock added wh=%s product=%s qty=%s new_qty=%s", warehouse_id, product_id, quantity, new_qty)
        self.metrics.inc("stock_add_total")
        return {
            "warehouse_id": warehouse_id,
            "product_id": product_id,
            "quantity": new_qty,
            "version": new_version,
            "threshold": eff_threshold,
        }

    def adjust_stock(self, warehouse_id, product_id, delta, actor="system", reason=None):
        if not warehouse_id or not product_id:
            raise ValidationError("warehouse_id and product_id are required")
        if not isinstance(delta, int) or delta == 0:
            raise ValidationError("delta must be a non-zero integer")
        now = _now()
        with self.db.lock:
            self._ensure_warehouse_product(warehouse_id, product_id)
            row = self._get_stock_row(warehouse_id, product_id)
            if row is None:
                raise NotFoundError("stock item not found for given warehouse/product")
            new_qty = row["quantity"] + delta
            if new_qty < 0:
                raise ValidationError("insufficient stock: resulting quantity would be negative")
            new_version = row["version"] + 1
            cur = self.db.conn.execute(
                "UPDATE stock_items SET quantity=?, version=?, updated_at=?"
                " WHERE warehouse_id=? AND product_id=? AND version=?",
                (new_qty, new_version, now, warehouse_id, product_id, row["version"]),
            )
            if cur.rowcount != 1:
                raise ConflictError("concurrent modification detected, please retry")
            self.db.conn.execute(
                "INSERT INTO stock_adjustments(id,warehouse_id,product_id,delta,resulting_quantity,"
                "reason,actor,created_at,idempotency_key) VALUES (?,?,?,?,?,?,?,?,NULL)",
                (_new_id(), warehouse_id, product_id, delta, new_qty, reason or "ADJUST", actor, now),
            )
            eff_threshold = self._effective_threshold(row["threshold"])
            alert_triggered = self._evaluate_alert(warehouse_id, product_id, new_qty, eff_threshold)
            self.db.conn.commit()
            logger.info("stock adjusted wh=%s product=%s delta=%s new_qty=%s", warehouse_id, product_id, delta, new_qty)
        self.metrics.inc("stock_adjust_total")
        return {
            "warehouse_id": warehouse_id,
            "product_id": product_id,
            "quantity": new_qty,
            "version": new_version,
            "alert_triggered": alert_triggered,
        }

    def get_stock(self, warehouse_id, product_id):
        with self.db.lock:
            self._ensure_warehouse_product(warehouse_id, product_id)
            row = self._get_stock_row(warehouse_id, product_id)
            if not row:
                raise NotFoundError("stock item not found")
            eff = self._effective_threshold(row["threshold"])
        return {
            "warehouse_id": warehouse_id,
            "product_id": product_id,
            "quantity": row["quantity"],
            "threshold": eff,
            "version": row["version"],
            "updated_at": row["updated_at"],
        }

    def list_stock_by_product(self, product_id):
        with self.db.lock:
            if not self.db.conn.execute("SELECT 1 FROM products WHERE id=?", (product_id,)).fetchone():
                raise NotFoundError(f"product {product_id} not found")
            rows = self.db.conn.execute(
                "SELECT warehouse_id,quantity,threshold FROM stock_items WHERE product_id=?",
                (product_id,),
            ).fetchall()
            return [
                {"warehouse_id": r["warehouse_id"], "quantity": r["quantity"],
                 "threshold": self._effective_threshold(r["threshold"])}
                for r in rows
            ]

    def list_stock_by_warehouse(self, warehouse_id):
        with self.db.lock:
            if not self.db.conn.execute("SELECT 1 FROM warehouses WHERE id=?", (warehouse_id,)).fetchone():
                raise NotFoundError(f"warehouse {warehouse_id} not found")
            rows = self.db.conn.execute(
                "SELECT product_id,quantity,threshold FROM stock_items WHERE warehouse_id=?",
                (warehouse_id,),
            ).fetchall()
            return [
                {"product_id": r["product_id"], "quantity": r["quantity"],
                 "threshold": self._effective_threshold(r["threshold"])}
                for r in rows
            ]

    # -------------------------------------------------------- thresholds
    def set_threshold(self, warehouse_id, product_id, threshold):
        if not isinstance(threshold, int) or threshold < 0:
            raise ValidationError("threshold must be a non-negative integer")
        now = _now()
        with self.db.lock:
            self._ensure_warehouse_product(warehouse_id, product_id)
            row = self._get_stock_row(warehouse_id, product_id)
            if row is None:
                self.db.conn.execute(
                    "INSERT INTO stock_items(warehouse_id,product_id,quantity,version,threshold,updated_at)"
                    " VALUES (?,?,0,1,?,?)",
                    (warehouse_id, product_id, threshold, now),
                )
            else:
                self.db.conn.execute(
                    "UPDATE stock_items SET threshold=?, updated_at=? WHERE warehouse_id=? AND product_id=?",
                    (threshold, now, warehouse_id, product_id),
                )
            self.db.conn.commit()
        return {"warehouse_id": warehouse_id, "product_id": product_id, "threshold": threshold}

    def set_default_threshold(self, value):
        if not isinstance(value, int) or value < 0:
            raise ValidationError("default_threshold must be a non-negative integer")
        with self.db.lock:
            self.db.conn.execute("UPDATE global_threshold SET default_threshold=? WHERE id=1", (value,))
            self.db.conn.commit()
        return {"default_threshold": value}

    # ------------------------------------------------------------- alerts
    def _resolve_active_alerts(self, warehouse_id, product_id):
        self.db.conn.execute(
            "UPDATE alerts SET status='RESOLVED', resolved_at=? WHERE warehouse_id=? AND product_id=? AND status='ACTIVE'",
            (_now(), warehouse_id, product_id),
        )

    def _evaluate_alert(self, warehouse_id, product_id, quantity, threshold):
        if threshold is None:
            self._resolve_active_alerts(warehouse_id, product_id)
            return False
        if quantity < threshold:
            existing = self.db.conn.execute(
                "SELECT id FROM alerts WHERE warehouse_id=? AND product_id=? AND status='ACTIVE'",
                (warehouse_id, product_id),
            ).fetchone()
            if not existing:
                aid, now = _new_id(), _now()
                self.db.conn.execute(
                    "INSERT INTO alerts(id,warehouse_id,product_id,threshold,quantity_at_trigger,"
                    "status,created_at,resolved_at) VALUES (?,?,?,?,?,'ACTIVE',?,NULL)",
                    (aid, warehouse_id, product_id, threshold, quantity, now),
                )
                self.event_bus.publish("low_stock_alert", {
                    "alert_id": aid, "warehouse_id": warehouse_id, "product_id": product_id,
                    "quantity": quantity, "threshold": threshold,
                })
                self.metrics.inc("alerts_triggered_total")
                logger.info("low stock alert triggered id=%s wh=%s product=%s qty=%s threshold=%s",
                            aid, warehouse_id, product_id, quantity, threshold)
            return True
        self._resolve_active_alerts(warehouse_id, product_id)
        return False

    def list_alerts(self, status=None):
        with self.db.lock:
            if status:
                rows = self.db.conn.execute(
                    "SELECT * FROM alerts WHERE status=? ORDER BY created_at DESC", (status,)
                ).fetchall()
            else:
                rows = self.db.conn.execute("SELECT * FROM alerts ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def resolve_alert(self, alert_id):
        with self.db.lock:
            row = self.db.conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
            if not row:
                raise NotFoundError(f"alert {alert_id} not found")
            if row["status"] == "ACTIVE":
                now = _now()
                self.db.conn.execute(
                    "UPDATE alerts SET status='RESOLVED', resolved_at=? WHERE id=?", (now, alert_id)
                )
                self.db.conn.commit()
                resolved_at = now
            else:
                resolved_at = row["resolved_at"]
        return {"id": alert_id, "status": "RESOLVED", "resolved_at": resolved_at}

    # --------------------------------------------------------------- audit
    def get_history(self, warehouse_id, product_id):
        with self.db.lock:
            self._ensure_warehouse_product(warehouse_id, product_id)
            rows = self.db.conn.execute(
                "SELECT id,delta,resulting_quantity,reason,actor,created_at FROM stock_adjustments"
                " WHERE warehouse_id=? AND product_id=? ORDER BY created_at DESC",
                (warehouse_id, product_id),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------- idempotency
    def get_idempotent_response(self, key):
        if not key:
            return None
        with self.db.lock:
            row = self.db.conn.execute(
                "SELECT * FROM idempotency_keys WHERE key=?", (key,)
            ).fetchone()
        return dict(row) if row else None

    def store_idempotent_response(self, key, request_hash, status, body):
        if not key:
            return
        with self.db.lock:
            try:
                self.db.conn.execute(
                    "INSERT INTO idempotency_keys(key,request_hash,response_body,response_status,created_at)"
                    " VALUES (?,?,?,?,?)",
                    (key, request_hash, json.dumps(body), status, _now()),
                )
                self.db.conn.commit()
            except sqlite3.IntegrityError:
                pass
