"""HTTP API layer: stdlib http.server routing, auth enforcement,
JSON (de)serialization and idempotency handling for the adjust endpoint.
"""
import hashlib
import json
import logging
import re
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from .auth import resolve_role, role_allows
from .service import NotFoundError, ValidationError, ConflictError

logger = logging.getLogger("inventory")

# (method, compiled regex, handler suffix, required role or None)
ROUTES = [
    ("POST", re.compile(r"^/warehouses$"), "create_warehouse", "WRITE"),
    ("GET", re.compile(r"^/warehouses$"), "list_warehouses", "READ"),
    ("GET", re.compile(r"^/warehouses/(?P<warehouse_id>[^/]+)/stock$"), "list_stock_by_warehouse", "READ"),
    ("GET", re.compile(r"^/warehouses/(?P<warehouse_id>[^/]+)$"), "get_warehouse", "READ"),
    ("POST", re.compile(r"^/products$"), "create_product", "WRITE"),
    ("GET", re.compile(r"^/products$"), "list_products", "READ"),
    ("GET", re.compile(r"^/products/(?P<product_id>[^/]+)/stock$"), "list_stock_by_product", "READ"),
    ("GET", re.compile(r"^/products/(?P<product_id>[^/]+)$"), "get_product", "READ"),
    ("POST", re.compile(r"^/stock/adjust$"), "adjust_stock", "WRITE"),
    ("POST", re.compile(r"^/stock$"), "add_stock", "WRITE"),
    ("GET", re.compile(r"^/stock/(?P<warehouse_id>[^/]+)/(?P<product_id>[^/]+)/history$"), "get_history", "READ"),
    ("GET", re.compile(r"^/stock/(?P<warehouse_id>[^/]+)/(?P<product_id>[^/]+)$"), "get_stock", "READ"),
    ("PUT", re.compile(r"^/thresholds/default$"), "set_default_threshold", "WRITE"),
    ("PUT", re.compile(r"^/thresholds/(?P<warehouse_id>[^/]+)/(?P<product_id>[^/]+)$"), "set_threshold", "WRITE"),
    ("GET", re.compile(r"^/alerts$"), "list_alerts", "READ"),
    ("POST", re.compile(r"^/alerts/(?P<alert_id>[^/]+)/resolve$"), "resolve_alert", "WRITE"),
]


class InventoryRequestHandler(BaseHTTPRequestHandler):
    server_version = "InventoryHTTP/1.0"

    def log_message(self, fmt, *args):
        pass  # request logging done explicitly via the "inventory" logger

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_text(self, status, text):
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            raise ValidationError("invalid JSON request body")

    def _dispatch(self, method):
        start = time.time()
        path = urlparse(self.path).path
        if method == "GET" and path == "/metrics":
            self._send_text(200, self.server.metrics.render())
            return
        service = self.server.service
        metrics = self.server.metrics
        for m, pattern, handler_name, role in ROUTES:
            if m != method:
                continue
            match = pattern.match(path)
            if not match:
                continue
            if role is not None:
                api_role = resolve_role(self.server.db, self.headers)
                if api_role is None:
                    self._send_json(401, {"error": "missing or invalid API key"})
                    return
                if not role_allows(api_role, role):
                    self._send_json(403, {"error": "insufficient permissions"})
                    return
            try:
                status, body = getattr(self, "h_" + handler_name)(match.groupdict(), service)
            except ValidationError as e:
                status, body = 400, {"error": str(e)}
            except NotFoundError as e:
                status, body = 404, {"error": str(e)}
            except ConflictError as e:
                status, body = 409, {"error": str(e)}
            except Exception:
                logger.exception("unhandled error")
                status, body = 500, {"error": "internal server error"}
            self._send_json(status, body)
            metrics.inc("requests_total")
            metrics.inc(f"status_{status}_total")
            logger.info("%s %s -> %s (%.1fms)", method, path, status, (time.time() - start) * 1000)
            return
        self._send_json(404, {"error": "not found"})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    # ---------------------------------------------------------- handlers
    def h_create_warehouse(self, params, service):
        body = self._read_json()
        return 201, service.create_warehouse(body.get("name"), body.get("location"))

    def h_list_warehouses(self, params, service):
        return 200, service.list_warehouses()

    def h_get_warehouse(self, params, service):
        return 200, service.get_warehouse(params["warehouse_id"])

    def h_create_product(self, params, service):
        body = self._read_json()
        return 201, service.create_product(body.get("sku"), body.get("name"), body.get("description"))

    def h_list_products(self, params, service):
        return 200, service.list_products()

    def h_get_product(self, params, service):
        return 200, service.get_product(params["product_id"])

    def h_add_stock(self, params, service):
        body = self._read_json()
        result = service.add_stock(
            body.get("warehouse_id"), body.get("product_id"), body.get("quantity"),
            body.get("threshold"), body.get("actor", "system"), body.get("reason"),
        )
        return 201, result

    def h_adjust_stock(self, params, service):
        body = self._read_json()
        idem_key = self.headers.get("Idempotency-Key")
        request_hash = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        if idem_key:
            cached = service.get_idempotent_response(idem_key)
            if cached:
                if cached["request_hash"] != request_hash:
                    raise ConflictError("idempotency key reused with a different request payload")
                return cached["response_status"], json.loads(cached["response_body"])
        result = service.adjust_stock(
            body.get("warehouse_id"), body.get("product_id"), body.get("delta"),
            body.get("actor", "system"), body.get("reason"),
        )
        status = 200
        if idem_key:
            service.store_idempotent_response(idem_key, request_hash, status, result)
        return status, result

    def h_get_stock(self, params, service):
        return 200, service.get_stock(params["warehouse_id"], params["product_id"])

    def h_list_stock_by_product(self, params, service):
        return 200, service.list_stock_by_product(params["product_id"])

    def h_list_stock_by_warehouse(self, params, service):
        return 200, service.list_stock_by_warehouse(params["warehouse_id"])

    def h_set_threshold(self, params, service):
        body = self._read_json()
        return 200, service.set_threshold(params["warehouse_id"], params["product_id"], body.get("threshold"))

    def h_set_default_threshold(self, params, service):
        body = self._read_json()
        return 200, service.set_default_threshold(body.get("default_threshold"))

    def h_list_alerts(self, params, service):
        qs = parse_qs(urlparse(self.path).query)
        status = qs.get("status", [None])[0]
        return 200, service.list_alerts(status)

    def h_resolve_alert(self, params, service):
        return 200, service.resolve_alert(params["alert_id"])

    def h_get_history(self, params, service):
        return 200, service.get_history(params["warehouse_id"], params["product_id"])
