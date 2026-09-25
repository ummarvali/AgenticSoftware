"""Unit and integration tests for rate limiting."""

import io
import json
import time
import unittest

from url_shortener.api import WSGIApp
from url_shortener.ratelimit import (
    EndpointRule,
    InMemorySlidingWindowBackend,
    RateLimiter,
    RateLimiterConfig,
)
from url_shortener.service import ShortenerService
from url_shortener.store import InMemoryStore


def call(app, method, path, body=None, extra_environ=None):
    raw = (body or "").encode("utf-8")
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "wsgi.input": io.BytesIO(raw),
        "CONTENT_LENGTH": str(len(raw)),
        "REMOTE_ADDR": "10.0.0.1",
    }
    if extra_environ:
        environ.update(extra_environ)
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    payload = b"".join(app(environ, start_response))
    return int(captured["status"].split()[0]), captured["headers"], payload


class SlidingWindowBackendTests(unittest.TestCase):
    def test_allows_up_to_limit_then_blocks(self):
        backend = InMemorySlidingWindowBackend()
        for _ in range(3):
            result = backend.check("k", 3, 60)
            self.assertTrue(result.allowed)
        result = backend.check("k", 3, 60)
        self.assertFalse(result.allowed)
        self.assertGreater(result.retry_after, 0)

    def test_window_expiry_allows_again(self):
        backend = InMemorySlidingWindowBackend()
        backend.check("k", 1, 0.05)
        blocked = backend.check("k", 1, 0.05)
        self.assertFalse(blocked.allowed)
        time.sleep(0.08)
        allowed_again = backend.check("k", 1, 0.05)
        self.assertTrue(allowed_again.allowed)

    def test_independent_keys(self):
        backend = InMemorySlidingWindowBackend()
        self.assertTrue(backend.check("a", 1, 60).allowed)
        self.assertTrue(backend.check("b", 1, 60).allowed)


class RateLimiterUnitTests(unittest.TestCase):
    def _limiter(self, limit=2, window=60, fail_mode="open", trusted_keys=frozenset(), multiplier=5.0):
        config = RateLimiterConfig(
            rules={"create": EndpointRule(limit, window)},
            fail_mode=fail_mode,
            trusted_keys=trusted_keys,
            trusted_multiplier=multiplier,
        )
        return RateLimiter(config)

    def test_blocks_after_limit_per_ip(self):
        limiter = self._limiter(limit=2)
        environ = {"REMOTE_ADDR": "1.2.3.4"}
        self.assertTrue(limiter.check("create", environ).allowed)
        self.assertTrue(limiter.check("create", environ).allowed)
        result = limiter.check("create", environ)
        self.assertFalse(result.allowed)
        self.assertEqual(result.limit, 2)

    def test_different_ips_have_independent_buckets(self):
        limiter = self._limiter(limit=1)
        self.assertTrue(limiter.check("create", {"REMOTE_ADDR": "1.1.1.1"}).allowed)
        self.assertTrue(limiter.check("create", {"REMOTE_ADDR": "2.2.2.2"}).allowed)

    def test_trusted_api_key_gets_higher_limit(self):
        limiter = self._limiter(limit=1, trusted_keys=frozenset({"secret"}), multiplier=3.0)
        environ = {"REMOTE_ADDR": "1.1.1.1", "HTTP_X_API_KEY": "secret"}
        for _ in range(3):
            self.assertTrue(limiter.check("create", environ).allowed)
        self.assertFalse(limiter.check("create", environ).allowed)

    def test_unrecognised_api_key_falls_back_to_ip_not_trusted(self):
        limiter = self._limiter(limit=1, trusted_keys=frozenset({"secret"}))
        environ = {"REMOTE_ADDR": "9.9.9.9", "HTTP_X_API_KEY": "not-trusted"}
        self.assertTrue(limiter.check("create", environ).allowed)
        self.assertFalse(limiter.check("create", environ).allowed)

    def test_fail_open_on_backend_error(self):
        class BoomBackend:
            def check(self, key, limit, window_seconds):
                raise RuntimeError("boom")

        limiter = self._limiter(limit=1, fail_mode="open")
        limiter.backend = BoomBackend()
        result = limiter.check("create", {"REMOTE_ADDR": "1.1.1.1"})
        self.assertTrue(result.allowed)
        self.assertEqual(limiter.metrics_snapshot()["errors"], 1)

    def test_fail_closed_on_backend_error(self):
        class BoomBackend:
            def check(self, key, limit, window_seconds):
                raise RuntimeError("boom")

        limiter = self._limiter(limit=1, fail_mode="closed")
        limiter.backend = BoomBackend()
        result = limiter.check("create", {"REMOTE_ADDR": "1.1.1.1"})
        self.assertFalse(result.allowed)

    def test_metrics_track_allowed_and_blocked(self):
        limiter = self._limiter(limit=1)
        environ = {"REMOTE_ADDR": "5.5.5.5"}
        limiter.check("create", environ)
        limiter.check("create", environ)
        snapshot = limiter.metrics_snapshot()
        self.assertEqual(snapshot["allowed"], 1)
        self.assertEqual(snapshot["blocked"], 1)
        self.assertEqual(snapshot["by_endpoint"]["create"], {"allowed": 1, "blocked": 1})

    def test_unconfigured_endpoint_is_unmetered(self):
        limiter = self._limiter(limit=1)
        result = limiter.check("stats", {"REMOTE_ADDR": "5.5.5.5"})
        self.assertTrue(result.allowed)


class ApiRateLimitIntegrationTests(unittest.TestCase):
    def _app_with_limit(self, limit=2, window=60):
        service = ShortenerService(store=InMemoryStore(), base_url="http://sho.rt")
        config = RateLimiterConfig(
            rules={
                "create": EndpointRule(limit, window),
                "redirect": EndpointRule(limit, window),
            }
        )
        limiter = RateLimiter(config)
        return WSGIApp(service=service, limiter=limiter)

    def test_create_endpoint_returns_429_with_headers(self):
        app = self._app_with_limit(limit=1)
        status, _, _ = call(app, "POST", "/api/shorten", json.dumps({"url": "https://a.com/1"}))
        self.assertEqual(status, 201)
        status, headers, body = call(app, "POST", "/api/shorten", json.dumps({"url": "https://a.com/2"}))
        self.assertEqual(status, 429)
        self.assertIn("Retry-After", headers)
        self.assertEqual(headers["X-RateLimit-Limit"], "1")
        self.assertEqual(headers["X-RateLimit-Remaining"], "0")
        self.assertEqual(json.loads(body)["error"], "rate limit exceeded")

    def test_redirect_endpoint_rate_limited_independently(self):
        app = self._app_with_limit(limit=1)
        _, _, body = call(app, "POST", "/api/shorten", json.dumps({"url": "https://a.com/x"}))
        code = json.loads(body)["code"]
        status, _, _ = call(app, "GET", "/" + code)
        self.assertEqual(status, 302)
        status, _, _ = call(app, "GET", "/" + code)
        self.assertEqual(status, 429)

    def test_metrics_endpoint_reports_counts(self):
        app = self._app_with_limit(limit=1)
        call(app, "POST", "/api/shorten", json.dumps({"url": "https://a.com/m"}))
        call(app, "POST", "/api/shorten", json.dumps({"url": "https://a.com/m2"}))
        status, _, body = call(app, "GET", "/metrics")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["rate_limit"]["by_endpoint"]["create"]["blocked"], 1)

    def test_default_app_has_rate_limiting_enabled(self):
        app = WSGIApp()
        self.assertIsNotNone(app.limiter)

    def test_limiter_false_disables_rate_limiting(self):
        service = ShortenerService(store=InMemoryStore())
        app = WSGIApp(service=service, limiter=False)
        for i in range(5):
            status, _, _ = call(app, "POST", "/api/shorten", json.dumps({"url": f"https://a.com/{i}"}))
            self.assertEqual(status, 201)


if __name__ == "__main__":
    unittest.main()
