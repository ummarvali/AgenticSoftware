"""HTTP API layer: routing, auth middleware, pagination helper."""
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from .service import InventoryService
from .openapi import OPENAPI_DOC

WRITE_METHODS = {"POST", "PUT"}

ROUTES = [
    ("GET", re.compile(r"^/openapi\.json$"), "openapi"),
    ("POST", re.compile(r"^/items$"), "create_item"),
    ("GET", re.compile(r"^/items$"), "list_items"),
    ("GET", re.compile(r"^/items/(?P<item_id>\d+)$"), "get_item"),
    ("POST", re.compile(r"^/warehouses$"), "create_warehouse"),
    ("GET", re.compile(r"^/warehouses$"), "list_warehouses"),
    ("POST", re.compile(r"^/stock/add$"), "add_stock"),
    ("POST", re.compile(r"^/stock/adjust$"), "adjust_stock"),
    ("PUT", re.compile(r"^/stock/(?P<item_id>\d+)/(?P<warehouse_id>\d+)/threshold$"), "set_threshold"),
    ("GET", re.compile(r"^/stock/(?P<item_id>\d+)/(?P<warehouse_id>\d+)$"), "get_stock"),
    ("GET", re.compile(r"^/stock/(?P<item_id>\d+)$"), "get_stock_by_item"),
    ("GET", re.compile(r"^/stock$"), "list_stock"),
    ("GET", re.compile(r"^/alerts/low-stock$"), "low_stock_alerts"),
    ("GET", re.compile(r"^/alerts$"), "list_alerts"),
    ("GET", re.compile(r"^/audit/adjustments$"), "list_audit"),
]


def paginate(qs):
    page = int(qs.get("page", ["1"])[0])
    page_size = int(qs.get("page_size", ["20"])[0])
    return max(page, 1), max(min(page_size, 200), 1)


def make_handler(service: InventoryService):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw or b"{}")
            except json.JSONDecodeError:
                raise ValueError("invalid JSON body")

        def _dispatch(self, method):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            for m, pattern, name in ROUTES:
                if m != method:
                    continue
                match = pattern.match(parsed.path)
                if match:
                    if method in WRITE_METHODS and name != "openapi":
                        key = self.headers.get("X-API-Key")
                        if not service.verify_api_key(key):
                            self._send(401, {"error": "unauthorized"})
                            return
                    try:
                        code, payload = getattr(self, f"h_{name}")(match.groupdict(), qs)
                        self._send(code, payload)
                    except ValueError as e:
                        self._send(400, {"error": str(e)})
                    except LookupError as e:
                        self._send(404, {"error": str(e)})
                    except Exception as e:
                        self._send(500, {"error": str(e)})
                    return
            self._send(404, {"error": "not found"})

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        # -------- handlers --------
        def h_openapi(self, params, qs):
            return 200, OPENAPI_DOC

        def h_create_item(self, params, qs):
            b = self._body()
            if not b.get("sku") or not b.get("name"):
                raise ValueError("sku and name are required")
            return 201, service.create_item(b["sku"], b["name"], b.get("metadata"))

        def h_list_items(self, params, qs):
            page, size = paginate(qs)
            items, total = service.list_items(page, size)
            return 200, {"items": items, "page": page, "page_size": size, "total": total}

        def h_get_item(self, params, qs):
            return 200, service.get_item(int(params["item_id"]))

        def h_create_warehouse(self, params, qs):
            b = self._body()
            if not b.get("code") or not b.get("name"):
                raise ValueError("code and name are required")
            return 201, service.create_warehouse(b["code"], b["name"], b.get("location"))

        def h_list_warehouses(self, params, qs):
            page, size = paginate(qs)
            whs, total = service.list_warehouses(page, size)
            return 200, {"warehouses": whs, "page": page, "page_size": size, "total": total}

        def h_add_stock(self, params, qs):
            b = self._body()
            required = ("item_id", "warehouse_id", "quantity")
            if not all(k in b for k in required):
                raise ValueError("item_id, warehouse_id, quantity are required")
            return 200, service.add_stock(
                int(b["item_id"]), int(b["warehouse_id"]), int(b["quantity"]),
                b.get("actor", "system"), b.get("idempotency_key"),
            )

        def h_adjust_stock(self, params, qs):
            b = self._body()
            required = ("item_id", "warehouse_id", "delta")
            if not all(k in b for k in required):
                raise ValueError("item_id, warehouse_id, delta are required")
            return 200, service.adjust_stock(
                int(b["item_id"]), int(b["warehouse_id"]), int(b["delta"]),
                b.get("reason", ""), b.get("actor", "system"), b.get("idempotency_key"),
            )

        def h_get_stock(self, params, qs):
            return 200, service.get_stock(int(params["item_id"]), int(params["warehouse_id"]))

        def h_get_stock_by_item(self, params, qs):
            return 200, service.get_stock_by_item(int(params["item_id"]))

        def h_list_stock(self, params, qs):
            page, size = paginate(qs)
            item_id = qs.get("item_id", [None])[0]
            warehouse_id = qs.get("warehouse_id", [None])[0]
            records, total = service.list_stock(
                page, size,
                int(item_id) if item_id else None,
                int(warehouse_id) if warehouse_id else None,
            )
            return 200, {"records": records, "page": page, "page_size": size, "total": total}

        def h_set_threshold(self, params, qs):
            b = self._body()
            if "low_stock_threshold" not in b:
                raise ValueError("low_stock_threshold is required")
            return 200, service.set_threshold(
                int(params["item_id"]), int(params["warehouse_id"]), int(b["low_stock_threshold"])
            )

        def h_low_stock_alerts(self, params, qs):
            page, size = paginate(qs)
            records, total = service.list_alerts(page, size, low_stock_only=True)
            return 200, {"alerts": records, "page": page, "page_size": size, "total": total}

        def h_list_alerts(self, params, qs):
            page, size = paginate(qs)
            records, total = service.list_alerts(page, size, low_stock_only=False)
            return 200, {"alerts": records, "page": page, "page_size": size, "total": total}

        def h_list_audit(self, params, qs):
            page, size = paginate(qs)
            item_id = qs.get("item_id", [None])[0]
            records, total = service.list_audit(page, size, int(item_id) if item_id else None)
            return 200, {"records": records, "page": page, "page_size": size, "total": total}

    return Handler


def create_server(db_path, host="127.0.0.1", port=8000):
    service = InventoryService(db_path)
    handler_cls = make_handler(service)
    return HTTPServer((host, port), handler_cls)
