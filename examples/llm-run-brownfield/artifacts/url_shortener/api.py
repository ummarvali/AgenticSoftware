"""WSGI HTTP adapter over :class:`ShortenerService`.

Kept as a plain WSGI callable so it runs on the standard-library server and is
trivially testable without booting a socket (see ``tests/test_api.py``).
"""

from __future__ import annotations

import json
import logging
import time

from .analytics import AnalyticsService
from .ratelimit import RateLimiter, build_default_limiter
from .service import AliasError, InvalidURLError, ShortenerService

logger = logging.getLogger(__name__)

_REASON = {
    200: "OK",
    201: "Created",
    302: "Found",
    400: "Bad Request",
    404: "Not Found",
    429: "Too Many Requests",
    500: "Internal Server Error",
}


class WSGIApp:
    """Minimal router mapping HTTP requests onto the service.

    ``limiter`` is a :class:`~url_shortener.ratelimit.RateLimiter`-like object
    exposing ``check(rule_name, environ) -> RateLimitResult``. It guards link
    creation, redirects and admin/analytics routes with per-rule sliding
    window budgets, returning HTTP 429 with ``Retry-After`` when a client goes
    over budget and attaching ``X-RateLimit-*`` headers otherwise. Defaults to
    a config-driven in-memory limiter (see ``ratelimit.py``) when omitted.
    """

    def __init__(self, service: ShortenerService | None = None, limiter: RateLimiter | None = None) -> None:
        self.service = service or ShortenerService()
        self.analytics = AnalyticsService(self.service.store)
        self.limiter = limiter or build_default_limiter()

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")
        try:
            if path in ("/healthz", "/health"):
                return self._json(start_response, 200, {"status": "ok"})

            if path == "/admin/metrics" and method == "GET":
                return self._with_rate_limit(
                    environ, start_response, "admin", lambda sr, extra: self._metrics(sr, extra)
                )

            if path.startswith("/admin/config/rate-limits/") and method == "PUT":
                rule_name = path[len("/admin/config/rate-limits/") :]
                return self._with_rate_limit(
                    environ,
                    start_response,
                    "admin",
                    lambda sr, extra: self._update_rule(environ, sr, rule_name, extra),
                )

            if path == "/api/shorten" and method == "POST":
                return self._with_rate_limit(
                    environ,
                    start_response,
                    "shorten",
                    lambda sr, extra: self._shorten(environ, sr, extra),
                )

            if path.startswith("/api/stats/") and method == "GET":
                code = path[len("/api/stats/") :]
                return self._with_rate_limit(
                    environ,
                    start_response,
                    "redirect",
                    lambda sr, extra: self._stats(sr, code, extra),
                )

            if method == "GET" and path != "/" and "/" not in path[1:]:
                code = path[1:]
                return self._with_rate_limit(
                    environ,
                    start_response,
                    "redirect",
                    lambda sr, extra: self._redirect(environ, sr, code, extra),
                )

            return self._json(start_response, 404, {"error": "not found"})
        except (InvalidURLError, AliasError) as exc:
            return self._json(start_response, 400, {"error": str(exc)})
        except Exception:  # defensive: never leak internals to the client
            return self._json(start_response, 500, {"error": "internal error"})

    # -- rate limiting glue ---------------------------------------------------
    def _with_rate_limit(self, environ, start_response, rule_name, handler):
        result = self.limiter.check(rule_name, environ)
        headers = result.headers()
        if not result.allowed:
            now = time.time()
            retry_after = max(0, int(round((result.reset_at or now) - now)))
            response_headers = list(headers) + [("Retry-After", str(retry_after))]
            return self._json(
                start_response,
                429,
                {"error": "rate limit exceeded", "retry_after": retry_after},
                extra_headers=response_headers,
            )
        return handler(start_response, headers)

    def _metrics(self, start_response, extra_headers=None):
        payload = {
            "rate_limits": self.limiter.rules_snapshot(),
            "metrics": self.limiter.metrics.snapshot(),
        }
        return self._json(start_response, 200, payload, extra_headers=extra_headers)

    def _update_rule(self, environ, start_response, rule_name, extra_headers=None):
        try:
            data = json.loads(self._read_body(environ) or "{}")
        except json.JSONDecodeError:
            return self._json(
                start_response, 400, {"error": "invalid JSON body"}, extra_headers=extra_headers
            )
        limit = data.get("limit")
        window_seconds = data.get("window_seconds", data.get("window"))
        valid = (
            isinstance(limit, (int, float))
            and not isinstance(limit, bool)
            and limit > 0
            and isinstance(window_seconds, (int, float))
            and not isinstance(window_seconds, bool)
            and window_seconds > 0
        )
        if not valid:
            return self._json(
                start_response,
                400,
                {"error": "limit and window_seconds must be positive numbers"},
                extra_headers=extra_headers,
            )
        self.limiter.set_rule(rule_name, int(limit), float(window_seconds))
        rule = self.limiter.get_rule(rule_name)
        return self._json(
            start_response,
            200,
            {"rule": rule_name, "limit": rule.limit, "window_seconds": rule.window_seconds},
            extra_headers=extra_headers,
        )

    # -- request handlers -------------------------------------------------
    def _shorten(self, environ, start_response, extra_headers=None):
        try:
            data = json.loads(self._read_body(environ) or "{}")
        except json.JSONDecodeError:
            return self._json(
                start_response, 400, {"error": "invalid JSON body"}, extra_headers=extra_headers
            )
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
            extra_headers=extra_headers,
        )

    def _redirect(self, environ, start_response, code, extra_headers=None):
        long_url = self.service.resolve(
            code,
            referrer=environ.get("HTTP_REFERER"),
            user_agent=environ.get("HTTP_USER_AGENT"),
        )
        if long_url is None:
            return self._json(
                start_response, 404, {"error": "unknown or expired code"}, extra_headers=extra_headers
            )
        headers = [("Location", long_url), ("Content-Length", "0")]
        if extra_headers:
            headers.extend(extra_headers)
        start_response("302 Found", headers)
        return [b""]

    def _stats(self, start_response, code, extra_headers=None):
        stats = self.service.stats(code)
        if stats is None:
            return self._json(
                start_response, 404, {"error": "unknown code"}, extra_headers=extra_headers
            )
        stats["analytics"] = self.analytics.report(code)
        return self._json(start_response, 200, stats, extra_headers=extra_headers)

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
