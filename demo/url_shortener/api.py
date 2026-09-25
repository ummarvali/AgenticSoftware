"""WSGI HTTP adapter over :class:`ShortenerService`.

Kept as a plain WSGI callable so it runs on the standard-library server and is
trivially testable without booting a socket (see ``tests/test_api.py``).
"""

from __future__ import annotations

import json

from .analytics import AnalyticsService
from .ratelimit import build_rate_limiter
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


class WSGIApp:
    """Minimal router mapping HTTP requests onto the service.

    ``limiter`` is a :class:`url_shortener.ratelimit.RateLimiter` guarding the
    create and redirect endpoints (HTTP 429 when exceeded). If omitted, a
    default limiter built from environment/config-file settings is used, so
    rate limiting is on by default; pass ``limiter=False`` to disable it
    explicitly (e.g. for isolated unit tests of unrelated behaviour).
    """

    def __init__(self, service: ShortenerService | None = None, limiter=None) -> None:
        self.service = service or ShortenerService()
        self.analytics = AnalyticsService(self.service.store)
        if limiter is None:
            self.limiter = build_rate_limiter()
        elif limiter is False:
            self.limiter = None
        else:
            self.limiter = limiter

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")
        try:
            if path == "/healthz":
                return self._json(start_response, 200, {"status": "ok"})
            if path == "/metrics" and method == "GET":
                return self._json(start_response, 200, self._metrics_payload())
            if path == "/api/shorten" and method == "POST":
                blocked = self._enforce_rate_limit("create", environ, start_response)
                if blocked is not None:
                    return blocked
                return self._shorten(environ, start_response)
            if path.startswith("/api/stats/") and method == "GET":
                return self._stats(start_response, path[len("/api/stats/") :])
            if method == "GET" and path != "/" and "/" not in path[1:]:
                blocked = self._enforce_rate_limit("redirect", environ, start_response)
                if blocked is not None:
                    return blocked
                return self._redirect(environ, start_response, path[1:])
            return self._json(start_response, 404, {"error": "not found"})
        except (InvalidURLError, AliasError) as exc:
            return self._json(start_response, 400, {"error": str(exc)})
        except Exception:  # defensive: never leak internals to the client
            return self._json(start_response, 500, {"error": "internal error"})

    def _enforce_rate_limit(self, endpoint, environ, start_response):
        """Return a WSGI response iterable if the client is rate limited,
        else ``None`` to let the caller proceed."""
        if not self.limiter:
            return None
        result = self.limiter.check(endpoint, environ)
        if result.allowed:
            return None
        body = json.dumps({"error": "rate limit exceeded"}).encode("utf-8")
        retry_after = max(0, int(result.retry_after) + 1)
        headers = [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
            ("Retry-After", str(retry_after)),
            ("X-RateLimit-Limit", str(result.limit)),
            ("X-RateLimit-Remaining", str(result.remaining)),
        ]
        start_response(f"429 {_REASON[429]}", headers)
        return [body]

    def _metrics_payload(self) -> dict:
        if self.limiter and hasattr(self.limiter, "metrics_snapshot"):
            return {"rate_limit": self.limiter.metrics_snapshot()}
        return {"rate_limit": None}

    def _shorten(self, environ, start_response):
        try:
            data = json.loads(self._read_body(environ) or "{}")
        except json.JSONDecodeError:
            return self._json(start_response, 400, {"error": "invalid JSON body"})
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
        )

    def _redirect(self, environ, start_response, code):
        long_url = self.service.resolve(
            code,
            referrer=environ.get("HTTP_REFERER"),
            user_agent=environ.get("HTTP_USER_AGENT"),
        )
        if long_url is None:
            return self._json(start_response, 404, {"error": "unknown or expired code"})
        start_response("302 Found", [("Location", long_url), ("Content-Length", "0")])
        return [b""]

    def _stats(self, start_response, code):
        stats = self.service.stats(code)
        if stats is None:
            return self._json(start_response, 404, {"error": "unknown code"})
        stats["analytics"] = self.analytics.report(code)
        return self._json(start_response, 200, stats)

    @staticmethod
    def _read_body(environ) -> str:
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return ""
        return environ["wsgi.input"].read(length).decode("utf-8")

    def _json(self, start_response, status, payload):
        body = json.dumps(payload).encode("utf-8")
        start_response(
            f"{status} {_REASON.get(status, 'OK')}",
            [("Content-Type", "application/json"), ("Content-Length", str(len(body)))],
        )
        return [body]


app = WSGIApp()
