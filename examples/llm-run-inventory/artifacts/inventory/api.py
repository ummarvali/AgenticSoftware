"""WSGI REST API for the inventory service."""
import json
import re
from dataclasses import asdict
from urllib.parse import urlparse, parse_qs

from .service import InventoryService, NotFoundError, ValidationError, ConflictError

_STATUS_TEXT = {
    200: "OK", 201: "Created", 400: "Bad Request", 404: "Not Found",
    409: "Conflict", 500: "Internal Server Error",
}


def _to_dict(obj):
    return asdict(obj)


class InventoryAPI:
    def __init__(self, service=None):
        self.service = service or InventoryService()
        self.routes = [
            ("POST", re.compile(r"^/products$"), self.create_product),
            ("GET", re.compile(r"^/products/(?P<product_id>\d+)$"), self.get_product),
            ("POST", re.compile(r"^/warehouses$"), self.create_warehouse),
            ("GET", re.compile(r"^/warehouses/(?P<warehouse_id>\d+)$"), self.get_warehouse),
            ("POST", re.compile(r"^/stock/adjustments$"), self.adjust_stock),
            ("POST", re.compile(r"^/stock$"), self.create_stock),
            ("GET", re.compile(r"^/stock$"), self.list_stock),
            ("GET", re.compile(r"^/stock/(?P<warehouse_id>\d+)/(?P<product_id>\d+)$"), self.get_stock),
            ("PUT", re.compile(r"^/thresholds/(?P<product_id>\d+)/(?P<warehouse_id>\d+)$"), self.set_threshold),
            ("GET", re.compile(r"^/alerts$"), self.list_alerts),
            ("POST", re.compile(r"^/alerts/(?P<alert_id>\d+)/resolve$"), self.resolve_alert),
        ]

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        path = urlparse(environ.get("PATH_INFO", "/")).path
        query = parse_qs(urlparse(environ.get("QUERY_STRING", "")).query)
        body = self._read_body(environ)
        for m, pattern, handler in self.routes:
            if m != method:
                continue
            match = pattern.match(path)
            if match:
                try:
                    status, payload = handler(match.groupdict(), query, body)
                except NotFoundError as e:
                    status, payload = 404, {"error": str(e)}
                except ValidationError as e:
                    status, payload = 400, {"error": str(e)}
                except ConflictError as e:
                    status, payload = 409, {"error": str(e)}
                except Exception as e:
                    status, payload = 500, {"error": str(e)}
                return self._respond(start_response, status, payload)
        return self._respond(start_response, 404, {"error": "not found"})

    def _read_body(self, environ):
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = environ["wsgi.input"].read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _respond(self, start_response, status, payload):
        body = json.dumps(payload).encode("utf-8")
        reason = _STATUS_TEXT.get(status, "OK")
        start_response(f"{status} {reason}", [("Content-Type", "application/json"),
                                                ("Content-Length", str(len(body)))])
        return [body]

    # Handlers
    def create_product(self, params, query, body):
        p = self.service.create_product(body.get("sku"), body.get("name"), body.get("description", ""))
        d = _to_dict(p)
        return 201, {"id": d["id"], "sku": d["sku"], "name": d["name"],
                     "description": d["description"], "created_at": d["created_at"]}

    def get_product(self, params, query, body):
        p = self.service.get_product(int(params["product_id"]))
        return 200, _to_dict(p)

    def create_warehouse(self, params, query, body):
        w = self.service.create_warehouse(body.get("code"), body.get("name"), body.get("location", ""))
        d = _to_dict(w)
        return 201, {"id": d["id"], "code": d["code"], "name": d["name"],
                     "location": d["location"], "created_at": d["created_at"]}

    def get_warehouse(self, params, query, body):
        w = self.service.get_warehouse(int(params["warehouse_id"]))
        return 200, _to_dict(w)

    def create_stock(self, params, query, body):
        item = self.service.create_stock_item(int(body.get("product_id")), int(body.get("warehouse_id")),
                                               int(body.get("quantity", 0)))
        d = _to_dict(item)
        return 201, {"id": d["id"], "product_id": d["product_id"], "warehouse_id": d["warehouse_id"],
                     "quantity": d["quantity"], "updated_at": d["updated_at"]}

    def adjust_stock(self, params, query, body):
        adjustment, item, triggered = self.service.adjust_stock(
            int(body.get("product_id")), int(body.get("warehouse_id")), int(body.get("delta")),
            body.get("reason", ""), body.get("actor", "system"), body.get("correlation_id"))
        return 201, {
            "adjustment_id": adjustment.id,
            "stock_item": {"product_id": item.product_id, "warehouse_id": item.warehouse_id,
                            "quantity": item.quantity},
            "alert_triggered": triggered,
        }

    def list_stock(self, params, query, body):
        page = int(query.get("page", ["1"])[0])
        page_size = int(query.get("page_size", ["20"])[0])
        items, total = self.service.list_stock(page, page_size)
        return 200, {
            "items": [{"product_id": i.product_id, "warehouse_id": i.warehouse_id,
                       "quantity": i.quantity, "reserved_quantity": i.reserved_quantity,
                       "updated_at": i.updated_at} for i in items],
            "total": total, "page": page, "page_size": page_size,
        }

    def get_stock(self, params, query, body):
        item = self.service.get_stock(int(params["warehouse_id"]), int(params["product_id"]))
        return 200, {"product_id": item.product_id, "warehouse_id": item.warehouse_id,
                      "quantity": item.quantity, "reserved_quantity": item.reserved_quantity,
                      "updated_at": item.updated_at}

    def set_threshold(self, params, query, body):
        t = self.service.set_threshold(int(params["product_id"]), int(params["warehouse_id"]),
                                        int(body.get("min_quantity")))
        return 200, {"product_id": t.product_id, "warehouse_id": t.warehouse_id,
                      "min_quantity": t.min_quantity, "updated_at": t.updated_at}

    def list_alerts(self, params, query, body):
        page = int(query.get("page", ["1"])[0])
        page_size = int(query.get("page_size", ["20"])[0])
        status = query.get("status", [None])[0]
        alerts, total = self.service.list_alerts(page, page_size, status)
        return 200, {
            "items": [{"id": a.id, "product_id": a.product_id, "warehouse_id": a.warehouse_id,
                       "current_quantity": a.current_quantity, "threshold_value": a.threshold_value,
                       "status": a.status, "created_at": a.created_at} for a in alerts],
            "total": total, "page": page, "page_size": page_size,
        }

    def resolve_alert(self, params, query, body):
        a = self.service.resolve_alert(int(params["alert_id"]), body.get("resolved_by", "user"))
        return 200, {"id": a.id, "status": a.status, "resolved_at": a.resolved_at,
                     "resolved_by": a.resolved_by}
