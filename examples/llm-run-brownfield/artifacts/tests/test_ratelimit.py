"""Tests for the rate limiting layer (unit + WSGI integration)."""

import io
import json
import unittest

from url_shortener.api import WSGIApp
from url_shortener.ratelimit import (
    InMemoryRateLimiterStore,
    RateLimiter,
    RateLimitRule,
)


def call(app, method, path, body=None, environ_extra=None):
    raw = (body or "").encode("utf-8")
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "wsgi.input": io.BytesIO(raw),
        "CONTENT_LENGTH": str(len(raw)),
        "REMOTE_ADDR": "127.0.0.1",
    }
    if environ_extra:
        environ.update(environ_extra)
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    payload = b"".join(app(environ, start_response))
    return int(captured["status"].split()[0]), captured["headers"], payload


class InMemoryStoreTests(unittest.TestCase):
    def test_allows_up_to_limit_then_denies(self):
        store = InMemoryRateLimiterStore()
        t = 1000.0
        for _ in range(3):
            allowed, remaining, _ = store.hit("k", 3, 60, now=t)
            self.assertTrue(allowed)
        allowed, remaining, reset_at = store.hit("k", 3, 60, now=t)
        self.assertFalse(allowed)
        self.assertEqual(remaining, 0)

    def test_window_resets_over_time(self):
        store = InMemoryRateLimiterStore()
        t = 1000.0
        for _ in range(2):
            self.assertTrue(store.hit("k", 2, 10, now=t)[0])
        self.assertFalse(store.hit("k", 2, 10, now=t)[0])
        # Move past the window: budget should be available again.
        allowed, _, _ = store.hit("k", 2, 10, now=t + 11)
        self.assertTrue(allowed)


class RateLimiterTests(unittest.TestCase):
    def _limiter(self, **rules):
        return RateLimiter(rules=rules)

    def test_check_enforces_named_rule(self):
        limiter = self._limiter(test=RateLimitRule(2, 60))
        environ = {"REMOTE_ADDR": "1.2.3.4"}
        r1 = limiter.check("test", environ, now=100.0)
        r2 = limiter.check("test", environ, now=100.0)
        r3 = limiter.check("test", environ, now=100.0)
        self.assertTrue(r1.allowed)
        self.assertTrue(r2.allowed)
        self.assertFalse(r3.allowed)
        self.assertEqual(r3.limit, 2)

    def test_unconfigured_rule_allows_without_headers(self):
        limiter = self._limiter()
        result = limiter.check("missing", {"REMOTE_ADDR": "1.1.1.1"})
        self.assertTrue(result.allowed)
        self.assertEqual(result.headers(), [])

    def test_metrics_track_allow_and_deny(self):
        limiter = self._limiter(test=RateLimitRule(1, 60))
        environ = {"REMOTE_ADDR": "9.9.9.9"}
        limiter.check("test", environ, now=1.0)
        limiter.check("test", environ, now=1.0)
        snap = limiter.metrics.snapshot()
        self.assertEqual(snap["test"]["allowed"], 1)
        self.assertEqual(snap["test"]["denied"], 1)

    def test_client_id_ignores_forwarded_for_by_default(self):
        limiter = self._limiter(test=RateLimitRule(5, 60))
        environ = {"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": "1.2.3.4"}
        self.assertEqual(limiter.client_id(environ), "10.0.0.1")

    def test_client_id_trusts_forwarded_for_when_configured(self):
        limiter = RateLimiter(
            rules={"test": RateLimitRule(5, 60)}, trusted_proxies={"10.0.0.1"}
        )
        environ = {"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": "1.2.3.4, 10.0.0.1"}
        self.assertEqual(limiter.client_id(environ), "1.2.3.4")

    def test_set_rule_hot_reloads_limit(self):
        limiter = self._limiter(test=RateLimitRule(5, 60))
        limiter.set_rule("test", 1, 60)
        environ = {"REMOTE_ADDR": "5.5.5.5"}
        self.assertTrue(limiter.check("test", environ, now=0.0).allowed)
        self.assertFalse(limiter.check("test", environ, now=0.0).allowed)


class RateLimitIntegrationTests(unittest.TestCase):
    def _app(self, shorten_limit=1, redirect_limit=100, admin_limit=30, window=60):
        limiter = RateLimiter(
            rules={
                "shorten": RateLimitRule(shorten_limit, window),
                "redirect": RateLimitRule(redirect_limit, window),
                "admin": RateLimitRule(admin_limit, window),
            }
        )
        return WSGIApp(limiter=limiter)

    def test_shorten_within_limit_has_headers(self):
        app = self._app(shorten_limit=5)
        status, headers, _ = call(
            app, "POST", "/api/shorten", json.dumps({"url": "https://example.com/a"})
        )
        self.assertEqual(status, 201)
        self.assertEqual(headers["X-RateLimit-Limit"], "5")

    def test_shorten_over_limit_returns_429(self):
        app = self._app(shorten_limit=1)
        status1, _, _ = call(
            app, "POST", "/api/shorten", json.dumps({"url": "https://example.com/a"})
        )
        status2, headers2, body2 = call(
            app, "POST", "/api/shorten", json.dumps({"url": "https://example.com/b"})
        )
        self.assertEqual(status1, 201)
        self.assertEqual(status2, 429)
        self.assertIn("Retry-After", headers2)
        self.assertEqual(json.loads(body2)["error"], "rate limit exceeded")

    def test_redirect_rate_limited_independently_of_shorten(self):
        app = self._app(shorten_limit=1, redirect_limit=1)
        _, _, body = call(
            app, "POST", "/api/shorten", json.dumps({"url": "https://example.com/x"})
        )
        code = json.loads(body)["code"]
        status1, _, _ = call(app, "GET", "/" + code)
        status2, headers2, _ = call(app, "GET", "/" + code)
        self.assertEqual(status1, 302)
        self.assertEqual(status2, 429)
        self.assertIn("Retry-After", headers2)

    def test_admin_metrics_endpoint(self):
        app = self._app(shorten_limit=1)
        call(app, "POST", "/api/shorten", json.dumps({"url": "https://example.com/a"}))
        call(app, "POST", "/api/shorten", json.dumps({"url": "https://example.com/b"}))
        status, _, body = call(app, "GET", "/admin/metrics")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertIn("shorten", payload["metrics"])
        self.assertEqual(payload["metrics"]["shorten"]["allowed"], 1)
        self.assertEqual(payload["metrics"]["shorten"]["denied"], 1)
        self.assertEqual(payload["rate_limits"]["shorten"]["limit"], 1)

    def test_admin_update_rule_takes_effect(self):
        app = self._app(shorten_limit=1)
        status, _, body = call(
            app,
            "PUT",
            "/admin/config/rate-limits/shorten",
            json.dumps({"limit": 3, "window_seconds": 60}),
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["limit"], 3)
        # Now three shortens should succeed before a fourth is denied.
        for i in range(3):
            status, _, _ = call(
                app, "POST", "/api/shorten", json.dumps({"url": f"https://example.com/{i}"})
            )
            self.assertEqual(status, 201)
        status, _, _ = call(
            app, "POST", "/api/shorten", json.dumps({"url": "https://example.com/last"})
        )
        self.assertEqual(status, 429)

    def test_admin_update_rule_rejects_bad_input(self):
        app = self._app()
        status, _, _ = call(
            app, "PUT", "/admin/config/rate-limits/shorten", json.dumps({"limit": -1})
        )
        self.assertEqual(status, 400)

    def test_health_alias(self):
        app = self._app()
        status, _, _ = call(app, "GET", "/health")
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
