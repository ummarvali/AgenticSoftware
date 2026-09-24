"""Core business logic for the inventory service."""
from datetime import datetime, timezone

from . import models
from .store import Store


def _now():
    return datetime.now(timezone.utc).isoformat()


class NotFoundError(Exception):
    pass


class ValidationError(Exception):
    pass


class ConflictError(Exception):
    pass


class InventoryService:
    def __init__(self, store=None):
        self.store = store or Store()

    # Products
    def create_product(self, sku, name, description=""):
        if not sku or not name:
            raise ValidationError("sku and name are required")
        pid = self.store.next_id("product")
        now = _now()
        product = models.Product(id=pid, sku=sku, name=name, description=description,
                                  created_at=now, updated_at=now)
        return self.store.add_product(product)

    def get_product(self, product_id):
        product = self.store.get_product(product_id)
        if not product:
            raise NotFoundError("product not found")
        return product

    # Warehouses
    def create_warehouse(self, code, name, location=""):
        if not code or not name:
            raise ValidationError("code and name are required")
        wid = self.store.next_id("warehouse")
        now = _now()
        warehouse = models.Warehouse(id=wid, code=code, name=name, location=location,
                                     created_at=now, updated_at=now)
        return self.store.add_warehouse(warehouse)

    def get_warehouse(self, warehouse_id):
        warehouse = self.store.get_warehouse(warehouse_id)
        if not warehouse:
            raise NotFoundError("warehouse not found")
        return warehouse

    # Stock
    def create_stock_item(self, product_id, warehouse_id, quantity=0):
        self.get_product(product_id)
        self.get_warehouse(warehouse_id)
        if quantity < 0:
            raise ValidationError("quantity cannot be negative")
        if self.store.find_stock_item(product_id, warehouse_id):
            raise ConflictError("stock item already exists")
        sid = self.store.next_id("stock_item")
        now = _now()
        item = models.StockItem(id=sid, product_id=product_id, warehouse_id=warehouse_id,
                                 quantity=quantity, reserved_quantity=0, version=1, updated_at=now)
        return self.store.add_stock_item(item)

    def get_stock(self, warehouse_id, product_id):
        item = self.store.find_stock_item(product_id, warehouse_id)
        if not item:
            raise NotFoundError("stock item not found")
        return item

    def list_stock(self, page=1, page_size=20):
        items = sorted(self.store.list_stock_items(), key=lambda i: i.id)
        total = len(items)
        start = (page - 1) * page_size
        end = start + page_size
        return items[start:end], total

    def adjust_stock(self, product_id, warehouse_id, delta, reason="", actor="system", correlation_id=None):
        item = self.store.find_stock_item(product_id, warehouse_id)
        if not item:
            raise NotFoundError("stock item not found")
        new_quantity = item.quantity + delta
        if new_quantity < 0:
            raise ValidationError("resulting quantity cannot be negative")
        item.quantity = new_quantity
        item.version += 1
        item.updated_at = _now()

        aid = self.store.next_id("adjustment")
        adjustment = models.StockAdjustment(
            id=aid, stock_item_id=item.id, delta=delta, resulting_quantity=new_quantity,
            reason=reason, actor=actor, correlation_id=correlation_id, created_at=_now())
        self.store.add_adjustment(adjustment)

        alert_triggered = self._evaluate_threshold(product_id, warehouse_id, new_quantity)
        return adjustment, item, alert_triggered

    def _evaluate_threshold(self, product_id, warehouse_id, quantity):
        threshold = self.store.find_threshold(product_id, warehouse_id)
        if threshold is None:
            threshold = self.store.find_threshold(product_id, None)
        if threshold is None:
            return False
        active = self.store.find_active_alert(product_id, warehouse_id)
        if quantity < threshold.min_quantity:
            if active:
                return False
            aid = self.store.next_id("alert")
            alert = models.Alert(id=aid, product_id=product_id, warehouse_id=warehouse_id,
                                  current_quantity=quantity, threshold_value=threshold.min_quantity,
                                  status="ACTIVE", created_at=_now())
            self.store.add_alert(alert)
            return True
        else:
            if active:
                active.status = "RESOLVED"
                active.resolved_at = _now()
                active.resolved_by = "system"
            return False

    # Thresholds
    def set_threshold(self, product_id, warehouse_id, min_quantity):
        self.get_product(product_id)
        if warehouse_id is not None:
            self.get_warehouse(warehouse_id)
        if min_quantity < 0:
            raise ValidationError("min_quantity cannot be negative")
        tid = self.store.next_id("threshold")
        now = _now()
        threshold = models.Threshold(id=tid, product_id=product_id, warehouse_id=warehouse_id,
                                      min_quantity=min_quantity, created_at=now, updated_at=now)
        return self.store.upsert_threshold(threshold)

    # Alerts
    def list_alerts(self, page=1, page_size=20, status=None):
        alerts = sorted(self.store.list_alerts(), key=lambda a: a.id)
        if status:
            alerts = [a for a in alerts if a.status == status]
        total = len(alerts)
        start = (page - 1) * page_size
        end = start + page_size
        return alerts[start:end], total

    def resolve_alert(self, alert_id, resolved_by="user"):
        alert = self.store.get_alert(alert_id)
        if not alert:
            raise NotFoundError("alert not found")
        if alert.status == "RESOLVED":
            raise ConflictError("alert already resolved")
        alert.status = "RESOLVED"
        alert.resolved_at = _now()
        alert.resolved_by = resolved_by
        return alert
