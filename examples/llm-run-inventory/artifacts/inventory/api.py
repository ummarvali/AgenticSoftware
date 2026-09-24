"""HTTP API layer built on stdlib http.server with simple regex routing."""
import json
import re
from http.server import BaseHTTPRequestHandler
from .service import ServiceError

METRICS = {"requests": 0, "errors": 0}
ROUTES = []


def route(method, pattern):
    regex = re.compile("^" + pattern + "$")

    def deco(fn):
        ROUTES.append((method, regex, fn))
        return fn
    return deco


def make_handler(service):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _auth(self):
            auth = self.headers.get("Authorization", "")
            key = auth.replace("Bearer ", "").strip()
            return service.check_api_key(key)

        def _body(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length == 0:
                return {}
            return json.loads(self.rfile.read(length))

        def _dispatch(self, method):
            METRICS["requests"] += 1
            path = self.path.split("?")[0]
            if path == "/metrics":
                self._send(200, METRICS)
                return
            if not self._auth():
                METRICS["errors"] += 1
                self._send(401, {"error": "unauthorized"})
                return
            for m, regex, fn in ROUTES:
                if m != method:
                    continue
                match = regex.match(path)
                if match:
                    try:
                        body = self._body() if method in ("POST", "PUT") else {}
                        result, code = fn(service, match.groups(), body)
                        self._send(code, result)
                    except ServiceError as e:
                        METRICS["errors"] += 1
                        self._send(e.status, {"error": e.message})
                    except Exception as e:
                        METRICS["errors"] += 1
                        self._send(400, {"error": str(e)})
                    return
            METRICS["errors"] += 1
            self._send(404, {"error": "not found"})

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        def log_message(self, fmt, *args):
            pass

    return Handler


@route("POST", r"/v1/warehouses")
def create_warehouse(service, groups, body):
    return service.create_warehouse(body.get("name"), body.get("location")), 201


@route("GET", r"/v1/warehouses")
def list_warehouses(service, groups, body):
    return service.list_warehouses(), 200


@route("POST", r"/v1/products")
def create_product(service, groups, body):
    return service.create_product(body.get("name"), body.get("unit")), 201


@route("GET", r"/v1/products")
def list_products(service, groups, body):
    return service.list_products(), 200


@route("POST", r"/v1/stock")
def add_stock(service, groups, body):
    r = service.add_stock(
        body.get("warehouse_id"), body.get("product_id"),
        body.get("quantity", 0), body.get("threshold"),
    )
    return r, 201


@route("POST", r"/v1/stock/adjustments")
def adjust_stock(service, groups, body):
    r = service.adjust_stock(
        body.get("warehouse_id"), body.get("product_id"),
        body.get("change_type"), body.get("amount"),
        body.get("reason"), body.get("actor", "system"),
        body.get("idempotency_key"),
    )
    return r, 201


@route("GET", r"/v1/stock/([^/]+)/([^/]+)")
def get_stock(service, groups, body):
    wid, pid = groups
    return service.get_stock(wid, pid), 200


@route("GET", r"/v1/products/([^/]+)/stock")
def stock_by_product(service, groups, body):
    return service.get_stock_by_product(groups[0]), 200


@route("GET", r"/v1/warehouses/([^/]+)/stock")
def stock_by_warehouse(service, groups, body):
    return service.get_stock_by_warehouse(groups[0]), 200


@route("PUT", r"/v1/thresholds/([^/]+)/([^/]+)")
def set_threshold(service, groups, body):
    wid, pid = groups
    return service.set_threshold(wid, pid, body.get("threshold")), 200


@route("GET", r"/v1/alerts")
def list_alerts(service, groups, body):
    return service.list_alerts(), 200


@route("GET", r"/v1/audit")
def list_audit(service, groups, body):
    return service.list_audit(), 200
