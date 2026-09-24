"""Core business logic: warehouses, products, stock, thresholds, alerts, audit."""
import uuid
import json
from . import db


class ServiceError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


class InventoryService:
    def __init__(self, conn):
        self.conn = conn

    def _new_id(self, prefix):
        return f"{prefix}-{uuid.uuid4().hex[:12]}"

    def check_api_key(self, key):
        if not key:
            return False
        row = self.conn.execute("SELECT 1 FROM api_keys WHERE key=?", (key,)).fetchone()
        return row is not None

    def create_warehouse(self, name, location):
        wid = self._new_id("wh")
        ts = db.now()
        self.conn.execute(
            "INSERT INTO warehouses(id,name,location,created_at) VALUES(?,?,?,?)",
            (wid, name, location, ts),
        )
        self.conn.commit()
        return {"id": wid, "name": name, "location": location, "created_at": ts}

    def list_warehouses(self):
        rows = self.conn.execute("SELECT id,name,location FROM warehouses").fetchall()
        return [dict(r) for r in rows]

    def create_product(self, name, unit):
        pid = self._new_id("pr")
        ts = db.now()
        self.conn.execute(
            "INSERT INTO products(id,name,unit,created_at) VALUES(?,?,?,?)",
            (pid, name, unit, ts),
        )
        self.conn.commit()
        return {"id": pid, "name": name, "unit": unit, "created_at": ts}

    def list_products(self):
        rows = self.conn.execute("SELECT id,name,unit FROM products").fetchall()
        return [dict(r) for r in rows]

    def _default_threshold(self):
        row = self.conn.execute(
            "SELECT value FROM global_settings WHERE key='default_low_stock_threshold'"
        ).fetchone()
        return int(row["value"]) if row else None

    def add_stock(self, warehouse_id, product_id, quantity, threshold=None):
        if quantity is None or quantity < 0:
            raise ServiceError("quantity must be a non-negative number")
        wh = self.conn.execute("SELECT id FROM warehouses WHERE id=?", (warehouse_id,)).fetchone()
        pr = self.conn.execute("SELECT id FROM products WHERE id=?", (product_id,)).fetchone()
        if not wh or not pr:
            raise ServiceError("warehouse or product not found", 404)
        existing = self.conn.execute(
            "SELECT * FROM stock_levels WHERE warehouse_id=? AND product_id=?",
            (warehouse_id, product_id),
        ).fetchone()
        if existing:
            raise ServiceError("stock already exists; use adjustments", 409)
        ts = db.now()
        self.conn.execute(
            "INSERT INTO stock_levels(warehouse_id,product_id,quantity,threshold,version,updated_at)"
            " VALUES(?,?,?,?,0,?)",
            (warehouse_id, product_id, quantity, threshold, ts),
        )
        self.conn.commit()
        return {
            "warehouse_id": warehouse_id,
            "product_id": product_id,
            "quantity": quantity,
            "threshold": threshold,
            "version": 0,
        }

    def set_threshold(self, warehouse_id, product_id, threshold):
        row = self.conn.execute(
            "SELECT * FROM stock_levels WHERE warehouse_id=? AND product_id=?",
            (warehouse_id, product_id),
        ).fetchone()
        if not row:
            raise ServiceError("stock record not found", 404)
        self.conn.execute(
            "UPDATE stock_levels SET threshold=? WHERE warehouse_id=? AND product_id=?",
            (threshold, warehouse_id, product_id),
        )
        self.conn.commit()
        return {"warehouse_id": warehouse_id, "product_id": product_id, "threshold": threshold}

    def get_stock(self, warehouse_id, product_id):
        row = self.conn.execute(
            "SELECT * FROM stock_levels WHERE warehouse_id=? AND product_id=?",
            (warehouse_id, product_id),
        ).fetchone()
        if not row:
            raise ServiceError("stock record not found", 404)
        return dict(row)

    def get_stock_by_product(self, product_id):
        rows = self.conn.execute(
            "SELECT warehouse_id,quantity,threshold FROM stock_levels WHERE product_id=?",
            (product_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stock_by_warehouse(self, warehouse_id):
        rows = self.conn.execute(
            "SELECT product_id,quantity,threshold FROM stock_levels WHERE warehouse_id=?",
            (warehouse_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_alerts(self):
        rows = self.conn.execute(
            "SELECT * FROM alerts WHERE status='ACTIVE' ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_audit(self):
        rows = self.conn.execute(
            "SELECT * FROM stock_adjustments ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def _idem_lookup(self, key):
        if not key:
            return None
        row = self.conn.execute(
            "SELECT response_body FROM idempotency_keys WHERE key=?", (key,)
        ).fetchone()
        return json.loads(row["response_body"]) if row else None

    def _idem_store(self, key, endpoint, response, status_code):
        if not key:
            return
        self.conn.execute(
            "INSERT OR REPLACE INTO idempotency_keys(key,endpoint,request_hash,response_body,"
            "status_code,created_at) VALUES(?,?,?,?,?,?)",
            (key, endpoint, "", json.dumps(response), status_code, db.now()),
        )

    def adjust_stock(self, warehouse_id, product_id, change_type, amount, reason=None,
                      actor="system", idempotency_key=None):
        cached = self._idem_lookup(idempotency_key)
        if cached is not None:
            return cached
        if amount is None or not isinstance(amount, (int, float)):
            raise ServiceError("amount is required and must be numeric")
        row = self.conn.execute(
            "SELECT * FROM stock_levels WHERE warehouse_id=? AND product_id=?",
            (warehouse_id, product_id),
        ).fetchone()
        if not row:
            raise ServiceError("stock record not found", 404)
        qty_before = row["quantity"]
        version = row["version"]
        ct = (change_type or "").upper()
        if ct == "INCREMENT":
            qty_after, delta = qty_before + amount, amount
        elif ct == "DECREMENT":
            qty_after, delta = qty_before - amount, -amount
        elif ct == "SET":
            qty_after, delta = amount, amount - qty_before
        else:
            raise ServiceError("invalid change_type; use INCREMENT/DECREMENT/SET")
        if qty_after < 0:
            raise ServiceError("adjustment would result in negative stock", 409)
        cur = self.conn.execute(
            "UPDATE stock_levels SET quantity=?, version=version+1, updated_at=? "
            "WHERE warehouse_id=? AND product_id=? AND version=?",
            (qty_after, db.now(), warehouse_id, product_id, version),
        )
        if cur.rowcount == 0:
            raise ServiceError("concurrent modification detected, retry", 409)
        adj_id = self._new_id("adj")
        self.conn.execute(
            "INSERT INTO stock_adjustments(id,warehouse_id,product_id,change_type,delta,"
            "quantity_before,quantity_after,reason,actor,idempotency_key,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (adj_id, warehouse_id, product_id, ct, delta, qty_before, qty_after,
             reason, actor, idempotency_key, db.now()),
        )
        threshold = row["threshold"]
        if threshold is None:
            threshold = self._default_threshold()
        alert_triggered = False
        if threshold is not None and qty_after <= threshold:
            active = self.conn.execute(
                "SELECT id FROM alerts WHERE warehouse_id=? AND product_id=? AND status='ACTIVE'",
                (warehouse_id, product_id),
            ).fetchone()
            if not active:
                aid = self._new_id("al")
                self.conn.execute(
                    "INSERT INTO alerts(id,warehouse_id,product_id,threshold,quantity_at_trigger,"
                    "status,created_at,resolved_at) VALUES(?,?,?,?,?,'ACTIVE',?,NULL)",
                    (aid, warehouse_id, product_id, threshold, qty_after, db.now()),
                )
            alert_triggered = True
        else:
            self.conn.execute(
                "UPDATE alerts SET status='RESOLVED', resolved_at=? "
                "WHERE warehouse_id=? AND product_id=? AND status='ACTIVE'",
                (db.now(), warehouse_id, product_id),
            )
        response = {
            "adjustment_id": adj_id,
            "warehouse_id": warehouse_id,
            "product_id": product_id,
            "quantity_before": qty_before,
            "quantity_after": qty_after,
            "alert_triggered": alert_triggered,
        }
        self._idem_store(idempotency_key, "/v1/stock/adjustments", response, 201)
        self.conn.commit()
        return response
