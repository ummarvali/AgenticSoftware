"""HTTP API layer using only the standard library http.server module."""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .app import Application

ANALYTICS_RE = re.compile(r"^/api/urls/([^/]+)/analytics$")
URL_ITEM_RE = re.compile(r"^/api/urls/([^/]+)$")


class Handler(BaseHTTPRequestHandler):
    app = None

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _client_id(self):
        return self.headers.get("X-API-Key") or self.client_address[0]

    def do_POST(self):
        if self.path == "/api/urls":
            if not self.app.limiter.allow(self._client_id()):
                return self._send(429, {"error": "rate limited"})
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                return self._send(400, {"error": "invalid json"})
            owner_key = self.headers.get("X-API-Key")
            status, payload = self.app.create_url(body, owner_key)
            return self._send(status, payload)
        return self._send(404, {"error": "not found"})

    def do_GET(self):
        if not self.app.limiter.allow(self._client_id()):
            return self._send(429, {"error": "rate limited"})
        m = ANALYTICS_RE.match(self.path)
        if m:
            status, payload = self.app.get_analytics(m.group(1))
            return self._send(status, payload)
        m = URL_ITEM_RE.match(self.path)
        if m:
            status, payload = self.app.get_url_info(m.group(1))
            return self._send(status, payload)
        if self.path in ("/", ""):
            return self._send(200, {"status": "ok"})
        short_code = self.path.lstrip("/")
        result = self.app.resolve(short_code)
        if result is None:
            return self._send(404, {"error": "not found"})
        if result == "EXPIRED":
            return self._send(410, {"error": "expired"})
        self.app.record_click(short_code, self.headers.get("Referer"), self.headers.get("User-Agent"))
        self.send_response(302)
        self.send_header("Location", result)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_DELETE(self):
        m = URL_ITEM_RE.match(self.path)
        if m:
            status, payload = self.app.delete_url(m.group(1))
            return self._send(status, payload)
        return self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        pass


def make_server(db_path=":memory:", host="127.0.0.1", port=0):
    app = Application(db_path)
    handler_cls = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer((host, port), handler_cls)
    return server, app


if __name__ == "__main__":
    srv, application = make_server(db_path="urls.db", port=8080)
    print("Serving on 8080")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        application.shutdown()
        srv.server_close()
