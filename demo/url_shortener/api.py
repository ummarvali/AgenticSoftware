"""WSGI HTTP adapter over :class:`ShortenerService`.

Kept as a plain WSGI callable so it runs on the standard-library server and is
trivially testable without booting a socket (see ``tests/test_api.py``).
"""

from __future__ import annotations

import json

from . import config
from .analytics import AnalyticsService
from .ratelimit import RateLimiter
from .service import AliasError, InvalidURLError, ShortenerService

_REASON = {
    200: "OK",
    201: "Created",
    302: "Found",
    400: "Bad Request",
    404: "Not Found",
    429: "Too Many Requests",
    500: "Internal Server Error",
}

# Sentinel so we can tell "limiter not passed" (-> build a config-driven default)
# apart from an explicit ``limiter=None`` (-> rate limiting disabled), which is
# handy for internal testing/bypass.
_UNSET = object()

# Endpoints that are exempt from rate limiting: liveness, observability, and the
# admin controls used to configure/reset the limiter itself must stay reachable
# even while a client is being throttled.
_EXEMPT_PATHS = {"/healthz", "/metrics", "/admin/rate-limit-config", "/admin/rate-limit-reset"}


class WSGIApp:
    """Minimal router mapping HTTP requests onto the service.

    ``limiter`` guards every non-exempt endpoint per client (IP by default, API
    key if an Authorization/X-API-Key header is present), returning HTTP 429
    with ``X-RateLimit-*`` headers once the configured threshold is exceeded.
    Pass an explicit ``limiter=None`` to disable rate limiting entirely (e.g.
    for focused unit tests); leave it unset to get a config-driven default.
    """

    def __init__(self, service: ShortenerService | None = None, limiter=_UNSET) -> None:
        self.service = service or ShortenerService()
        self.analytics = AnalyticsService(self.service.store)
        if limiter is _UNSET:
            self.limiter = RateLimiter(
                limit=config.RATE_LIMIT,
                window_seconds=config.RATE_LIMIT_WINDOW_SECONDS,
                allowlist=config.RATE_LIMIT_ALLOWLIST,
            )
        else:
            self.limiter = limiter

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")
        rl_headers = None
        try:
            if path == "/healthz":
                return self._json(start_response, 200, {"status": "ok"})
            if path == "/metrics" and method == "GET":
                return self._metrics(start_response)
            if path == "/admin/rate-limit-config":
                return self._rate_limit_config(environ, start_response, method)
            if path == "/admin/rate-limit-reset" and method == "POST":
                return self._rate_limit_reset(environ, start_response)

            if self.limiter is not None and path not in _EXEMPT_PATHS:
                key = self.limiter.identify(environ)
                decision = self.limiter.check(key)
                rl_headers = self._rate_limit_headers(decision)
                if not decision["allowed"]:
                    return self._json(
                        start_response, 429, {"error": "rate limit exceeded"}, rl_headers
                    )

            if path == "/api/shorten" and method == "POST":
                return self._shorten(environ, start_response, rl_headers)
            if path.startswith("/api/stats/") and method == "GET":
                return self._stats(start_response, path[len("/api/stats/") :], rl_headers)
            if method == "GET" and path != "/" and "/" not in path[1:]:
                return self._redirect(environ, start_response, path[1:], rl_headers)
            return self._json(start_response, 404, {"error": "not found"}, rl_headers)
        except (InvalidURLError, AliasError) as exc:
            return self._json(start_response, 400, {"error": str(exc)}, rl_headers)
        except Exception:  # defensive: never leak internals to the client
            return self._json(start_response, 500, {"error": "internal error"})

    def _shorten(self, environ, start_response, extra_headers=None):
        try:
            data = json.loads(self._read_body(environ) or "{}")
        except json.JSONDecodeError:
            return self._json(start_response, 400, {"error": "invalid JSON body"}, extra_headers)
        record = self.service.shorten(
            data.get("url"),
            custom_alias=data.get("alias"),
            ttl_seconds=data.get("ttl_seconds"),
        )
        return self._json(
            start_response,
            201,
            {
                "code": record.code,
                "short_url": self.service.short_url(record.code),
                "long_url": record.long_url,
                "expires_at": record.expires_at,
            },
            extra_headers,
        )

    def _redirect(self, environ, start_response, code, extra_headers=None):
        long_url = self.service.resolve(
            code,
            referrer=environ.get("HTTP_REFERER"),
            user_agent=environ.get("HTTP_USER_AGENT"),
        )
        if long_url is None:
            return self._json(
                start_response, 404, {"error": "unknown or expired code"}, extra_headers
            )
        headers = [("Location", long_url), ("Content-Length", "0")]
        if extra_headers:
            headers.extend(extra_headers)
        start_response("302 Found", headers)
        return [b""]

    def _stats(self, start_response, code, extra_headers=None):
        stats = self.service.stats(code)
        if stats is None:
            return self._json(start_response, 404, {"error": "unknown code"}, extra_headers)
        stats["analytics"] = self.analytics.report(code)
        return self._json(start_response, 200, stats, extra_headers)

    def _metrics(self, start_response):
        if self.limiter is None:
            payload = {"total_requests": 0, "blocked_requests": 0, "per_client_blocks": {}}
        else:
            payload = self.limiter.metrics.snapshot()
        return self._json(start_response, 200, payload)

    def _rate_limit_config(self, environ, start_response, method):
        if self.limiter is None:
            return self._json(start_response, 404, {"error": "rate limiting disabled"})
        if method == "GET":
            return self._json(start_response, 200, self.limiter.get_config())
        if method == "PUT":
            try:
                data = json.loads(self._read_body(environ) or "{}")
            except json.JSONDecodeError:
                return self._json(start_response, 400, {"error": "invalid JSON body"})
            updated = self.limiter.update_config(
                limit=data.get("limit"),
                window_seconds=data.get("window_seconds"),
                allowlist=data.get("allowlist"),
            )
            return self._json(start_response, 200, updated)
        return self._json(start_response, 404, {"error": "not found"})

    def _rate_limit_reset(self, environ, start_response):
        if self.limiter is None:
            return self._json(start_response, 404, {"error": "rate limiting disabled"})
        try:
            data = json.loads(self._read_body(environ) or "{}")
        except json.JSONDecodeError:
            data = {}
        self.limiter.reset(data.get("key"))
        return self._json(start_response, 200, {"status": "reset"})

    @staticmethod
    def _rate_limit_headers(decision: dict) -> list:
        return [
            ("X-RateLimit-Limit", str(decision["limit"])),
            ("X-RateLimit-Remaining", str(decision["remaining"])),
            ("X-RateLimit-Reset", str(decision["reset"])),
        ]

    @staticmethod
    def _read_body(environ) -> str:
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return ""
        return environ["wsgi.input"].read(length).decode("utf-8")

    def _json(self, start_response, status, payload, extra_headers=None):
        body = json.dumps(payload).encode("utf-8")
        headers = [("Content-Type", "application/json"), ("Content-Length", str(len(body)))]
        if extra_headers:
            headers.extend(extra_headers)
        start_response(f"{status} {_REASON.get(status, 'OK')}", headers)
        return [body]


app = WSGIApp()
