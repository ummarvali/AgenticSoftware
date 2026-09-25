"""HTTP API layer built on the standard library's http.server.

Exposes create/redirect/analytics/health/metrics endpoints. Only the create
and key-creation endpoints are rate limited; identity for rate limiting is
either a verified API key (checked against the database) or the raw peer
address -- an unverified client-supplied header is never trusted as identity.
"""
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .cache import LRUCache
from .config import config as default_config
from .ratelimit import RateLimiter
from .service import ExpiredError, NotFoundError, URLService
from .storage import Storage
from .validator import ValidationError

logger = logging.getLogger(__name__)

OPENAPI_SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "URL Shortener API", "version": "1.0.0"},
    "paths": {
        "/api/v1/urls": {"post": {"summary": "Create a short URL"}},
        "/{short_code}": {"get": {"summary": "Redirect to the original URL"}},
        "/api/v1/urls/{short_code}": {"get": {"summary": "Get URL metadata"}},
        "/api/v1/urls/{short_code}/analytics": {"get": {"summary": "Get click analytics"}},
        "/api/v1/keys": {"post": {"summary": "Create an API key"}},
        "/healthz": {"get": {"summary": "Health check"}},
        "/metrics": {"get": {"summary": "Prometheus-style metrics"}},
    },
}


def make_handler(service, limiter, cfg):
    """Build a BaseHTTPRequestHandler subclass bound to the given collaborators."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "URLShortener/1.0"
        _service = service
        _limiter = limiter
        _config = cfg

        def log_message(self, fmt, *args):
            logger.info("%s - %s", self.address_string(), fmt % args)

        def _send_json(self, status, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        def _rate_limit_identity(self):
            api_key = self.headers.get("X-API-Key")
            if api_key:
                row = self._service.verify_api_key(api_key)
                if row:
                    return f"key:{api_key}", row["rate_limit_per_min"], row["id"]
            return f"ip:{self.client_address[0]}", self._config.anonymous_rate_limit_per_min, None

        # ---- routing ----------------------------------------------------
        def do_GET(self):
            self._service.inc_metric("requests_total")
            path = urlsplit(self.path).path
            try:
                if path == "/healthz":
                    return self._handle_health()
                if path == "/metrics":
                    return self._handle_metrics()
                if path == "/openapi.json":
                    return self._send_json(200, OPENAPI_SPEC)
                if path.startswith("/api/v1/urls/") and path.endswith("/analytics"):
                    code = path[len("/api/v1/urls/"):-len("/analytics")]
                    return self._handle_get_analytics(code)
                if path.startswith("/api/v1/urls/"):
                    code = path[len("/api/v1/urls/"):]
                    return self._handle_get_url_info(code)
                if path.startswith("/api/v1/"):
                    return self._send_json(404, {"error": "not_found"})
                code = path.lstrip("/")
                if not code or "/" in code:
                    return self._send_json(404, {"error": "not_found"})
                return self._handle_redirect(code)
            except Exception:
                logger.exception("unhandled error handling GET %s", path)
                self._send_json(500, {"error": "internal_error"})

        def do_POST(self):
            self._service.inc_metric("requests_total")
            path = urlsplit(self.path).path
            try:
                if path == "/api/v1/urls":
                    return self._handle_create_url()
                if path == "/api/v1/keys":
                    return self._handle_create_key()
                return self._send_json(404, {"error": "not_found"})
            except Exception:
                logger.exception("unhandled error handling POST %s", path)
                self._send_json(500, {"error": "internal_error"})

        # ---- handlers -----------------------------------------------------
        def _handle_health(self):
            health = self._service.health()
            status_code = 200 if health["status"] == "ok" else 503
            self._send_json(status_code, health)

        def _handle_metrics(self):
            metrics = self._service.get_metrics()
            lines = []
            for name, value in metrics.items():
                metric_name = f"urlshortener_{name}"
                lines.append(f"# TYPE {metric_name} counter")
                lines.append(f"{metric_name} {value}")
            body = ("\n".join(lines) + "\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle_get_analytics(self, code):
            try:
                data = self._service.get_analytics(code)
            except NotFoundError:
                return self._send_json(404, {"error": "not_found"})
            self._send_json(200, data)

        def _handle_get_url_info(self, code):
            try:
                data = self._service.get_url_info(code)
            except NotFoundError:
                return self._send_json(404, {"error": "not_found"})
            self._send_json(200, data)

        def _handle_redirect(self, code):
            referrer = self.headers.get("Referer")
            user_agent = self.headers.get("User-Agent")
            try:
                long_url = self._service.resolve(
                    code,
                    referrer=referrer,
                    user_agent=user_agent,
                    client_addr=self.client_address[0],
                )
            except NotFoundError:
                self._service.inc_metric("redirect_not_found_total")
                return self._send_json(404, {"error": "not_found"})
            except ExpiredError:
                self._service.inc_metric("redirect_expired_total")
                return self._send_json(410, {"error": "expired"})
            self._service.inc_metric("redirects_total")
            self.send_response(302)
            self.send_header("Location", long_url)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _handle_create_url(self):
            identity, limit, api_key_id = self._rate_limit_identity()
            if not self._limiter.allow(identity, limit):
                return self._send_json(429, {"error": "rate_limited"})
            try:
                body = self._read_json_body()
            except (ValueError, UnicodeDecodeError):
                return self._send_json(400, {"error": "invalid request body"})
            if not isinstance(body, dict):
                return self._send_json(400, {"error": "invalid request body"})
            long_url = body.get("long_url")
            custom_alias = body.get("custom_alias")
            ttl_seconds = body.get("ttl_seconds")
            try:
                result = self._service.create_short_url(
                    long_url,
                    custom_alias=custom_alias,
                    ttl_seconds=ttl_seconds,
                    api_key_id=api_key_id,
                )
            except ValidationError as exc:
                return self._send_json(400, {"error": str(exc)})
            result["short_url"] = f"{self._config.base_url}/{result['short_code']}"
            self._service.inc_metric("urls_created_total")
            self._send_json(201, result)

        def _handle_create_key(self):
            identity = f"ip:{self.client_address[0]}"
            if not self._limiter.allow(identity, self._config.key_creation_rate_limit_per_min):
                return self._send_json(429, {"error": "rate_limited"})
            result = self._service.create_api_key()
            self._service.inc_metric("keys_created_total")
            self._send_json(201, result)

    return Handler


def build_server(cfg=None):
    """Construct storage/cache/service and return a ready-to-serve HTTPServer."""
    cfg = cfg or default_config
    storage = Storage(cfg.db_path)
    cache = LRUCache(cfg.cache_capacity)
    service = URLService(cfg, storage, cache)
    limiter = RateLimiter()
    handler_cls = make_handler(service, limiter, cfg)
    httpd = ThreadingHTTPServer((cfg.host, cfg.port), handler_cls)
    httpd.daemon_threads = True
    return httpd, service


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    httpd, _service = build_server(default_config)
    logger.info("URL shortener listening on %s:%s", default_config.host, default_config.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
