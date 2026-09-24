"""Thread-safe in-memory data store used as the source of truth."""
import itertools
import threading


class Store:
    def __init__(self):
        self._lock = threading.RLock()
        self._products = {}
        self._warehouses = {}
        self._stock_items = {}
        self._adjustments = {}
        self._thresholds = {}
        self._alerts = {}
        self._ids = {
            "product": itertools.count(1),
            "warehouse": itertools.count(1),
            "stock_item": itertools.count(1),
            "adjustment": itertools.count(1),
            "threshold": itertools.count(1),
            "alert": itertools.count(1),
        }

    def next_id(self, kind):
        with self._lock:
            return next(self._ids[kind])

    # Products
    def add_product(self, product):
        with self._lock:
            self._products[product.id] = product
            return product

    def get_product(self, product_id):
        return self._products.get(product_id)

    # Warehouses
    def add_warehouse(self, warehouse):
        with self._lock:
            self._warehouses[warehouse.id] = warehouse
            return warehouse

    def get_warehouse(self, warehouse_id):
        return self._warehouses.get(warehouse_id)

    # Stock items
    def add_stock_item(self, item):
        with self._lock:
            self._stock_items[item.id] = item
            return item

    def find_stock_item(self, product_id, warehouse_id):
        with self._lock:
            for item in self._stock_items.values():
                if item.product_id == product_id and item.warehouse_id == warehouse_id:
                    return item
            return None

    def list_stock_items(self):
        with self._lock:
            return list(self._stock_items.values())

    # Adjustments
    def add_adjustment(self, adj):
        with self._lock:
            self._adjustments[adj.id] = adj
            return adj

    # Thresholds
    def upsert_threshold(self, threshold):
        with self._lock:
            for existing in self._thresholds.values():
                if existing.product_id == threshold.product_id and existing.warehouse_id == threshold.warehouse_id:
                    existing.min_quantity = threshold.min_quantity
                    existing.updated_at = threshold.updated_at
                    return existing
            self._thresholds[threshold.id] = threshold
            return threshold

    def find_threshold(self, product_id, warehouse_id):
        with self._lock:
            for t in self._thresholds.values():
                if t.product_id == product_id and t.warehouse_id == warehouse_id:
                    return t
            return None

    # Alerts
    def add_alert(self, alert):
        with self._lock:
            self._alerts[alert.id] = alert
            return alert

    def get_alert(self, alert_id):
        return self._alerts.get(alert_id)

    def find_active_alert(self, product_id, warehouse_id):
        with self._lock:
            for a in self._alerts.values():
                if a.product_id == product_id and a.warehouse_id == warehouse_id and a.status == "ACTIVE":
                    return a
            return None

    def list_alerts(self):
        with self._lock:
            return list(self._alerts.values())
