"""HTTP API layer: a BaseHTTPRequestHandler that routes requests to the service layer."""
import json
import re
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

from .auth import verify_api_key, create_api_key
from .validation import ValidationError
from .service import ConflictError, PermissionDeniedError, NotFoundError

STATS_RE = re.compile(r"^/api/urls/([A-Za-z0-9_-]+)/stats$")
URL_ITEM_RE = re.compile(r"^/api/urls/([A-Za-z0-9_-]+)$")
SHORT_RE = re.compile(r"^/([A-Za-z0-9_-]+)$")


def make_handler(service, db, rate_limiter):
    """Build a request handler class bound to the given service/db/rate-limiter instances."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "URLShortener/1.0"

        def log_message(self, fmt, *args):
            pass  # keep test/CI output quiet

        def _send_json(self, status, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except ValueError:
                raise ValidationError("invalid JSON body")

        def _client_id(self):
            return self.headers.get("X-API-Key") or self.client_address[0]

        def _check_rate_limit(self):
            if not rate_limiter.allow(self._client_id()):
                self._send_json(429, {"error": "rate limit exceeded"})
                return False
            return True

        def _api_key(self):
            return self.headers.get("X-API-Key")

        def do_GET(self):
            if not self._check_rate_limit():
                return
            path = urlparse(self.path).path
            m = STATS_RE.match(path)
            if m:
                self._handle_stats(m.group(1))
                return
            if path.startswith("/api/"):
                self._send_json(404, {"error": "not found"})
                return
            m = SHORT_RE.match(path)
            if m:
                self._handle_redirect(m.group(1))
                return
            self._send_json(404, {"error": "not found"})

        def do_POST(self):
            if not self._check_rate_limit():
                return
            path = urlparse(self.path).path
            try:
                if path == "/api/urls":
                    self._handle_create()
                elif path == "/api/urls/bulk":
                    self._handle_bulk()
                elif path == "/api/keys":
                    self._handle_create_key()
                else:
                    self._send_json(404, {"error": "not found"})
            except ValidationError as exc:
                self._send_json(400, {"error": str(exc)})
            except ConflictError as exc:
                self._send_json(409, {"error": str(exc)})

        def do_PUT(self):
            if not self._check_rate_limit():
                return
            path = urlparse(self.path).path
            m = URL_ITEM_RE.match(path)
            if not m:
                self._send_json(404, {"error": "not found"})
                return
            try:
                self._handle_update(m.group(1))
            except ValidationError as exc:
                self._send_json(400, {"error": str(exc)})
            except PermissionDeniedError as exc:
                self._send_json(403, {"error": str(exc)})
            except NotFoundError as exc:
                self._send_json(404, {"error": str(exc)})

        def do_DELETE(self):
            if not self._check_rate_limit():
                return
            path = urlparse(self.path).path
            m = URL_ITEM_RE.match(path)
            if not m:
                self._send_json(404, {"error": "not found"})
                return
            try:
                self._handle_delete(m.group(1))
            except PermissionDeniedError as exc:
                self._send_json(403, {"error": str(exc)})
            except NotFoundError as exc:
                self._send_json(404, {"error": str(exc)})

        def _handle_create(self):
            body = self._read_json()
            result = service.create_url(
                body.get("long_url"),
                custom_alias=body.get("custom_alias"),
                expires_at=body.get("expires_at"),
                owner_key=self._api_key(),
            )
            self._send_json(201, result)

        def _handle_bulk(self):
            body = self._read_json()
            urls = body.get("urls")
            if not isinstance(urls, list) or not urls:
                self._send_json(400, {"error": "urls must be a non-empty list"})
                return
            results = service.create_bulk(urls, owner_key=self._api_key())
            self._send_json(201, {"results": results})

        def _handle_create_key(self):
            body = self._read_json()
            owner_name = body.get("owner_name")
            if not owner_name:
                self._send_json(400, {"error": "owner_name is required"})
                return
            result = create_api_key(db, owner_name)
            self._send_json(201, result)

        def _handle_redirect(self, code):
            long_url = service.resolve(
                code,
                referrer=self.headers.get("Referer"),
                user_agent=self.headers.get("User-Agent"),
                ip=self.client_address[0],
            )
            if long_url is None:
                self._send_json(404, {"error": "short code not found or expired"})
                return
            self.send_response(302)
            self.send_header("Location", long_url)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _handle_stats(self, code):
            stats = service.get_stats(code)
            if stats is None:
                self._send_json(404, {"error": "short code not found"})
                return
            self._send_json(200, stats)

        def _handle_update(self, code):
            body = self._read_json()
            result = service.update_url(
                code, self._api_key(), long_url=body.get("long_url"), expires_at=body.get("expires_at")
            )
            self._send_json(200, result)

        def _handle_delete(self, code):
            result = service.delete_url(code, self._api_key())
            self._send_json(200, result)

    return Handler
