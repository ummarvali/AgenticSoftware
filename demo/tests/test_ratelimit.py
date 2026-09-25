"""Tests for the rate limiting layer: unit tests on RateLimiter plus
integration tests driving it through the WSGI app end to end."""

import io
import json
import unittest

from url_shortener.api import WSGIApp
from url_shortener.ratelimit import InMemoryRateLimitStore, RateLimiter


def call(app, method, path, body=None, remote_addr="1.2.3.4", extra_environ=None):
    raw = (body or "").encode("utf-8")
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "wsgi.input": io.BytesIO(raw),
        "CONTENT_LENGTH": str(len(raw)),
        "REMOTE_ADDR": remote_addr,
    }
    if extra_environ:
        environ.update(extra_environ)
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    payload = b"".join(app(environ, start_response))
    return int(captured["status"].split()[0]), captured["headers"], payload


class FakeClock:
    """Deterministic clock so window-boundary behaviour is testable."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RateLimiterUnitTests(unittest.TestCase):
    def test_allows_up_to_limit_then_blocks(self):
        clock = FakeClock()
        limiter = RateLimiter(limit=3, window_seconds=10, clock=clock)
        for _ in range(3):
            self.assertTrue(limiter.allow("ip:1"))
        self.assertFalse(limiter.allow("ip:1"))

    def test_resets_on_new_window_boundary(self):
        clock = FakeClock()
        limiter = RateLimiter(limit=2, window_seconds=10, clock=clock)
        self.assertTrue(limiter.allow("ip:1"))
        self.assertTrue(limiter.allow("ip:1"))
        self.assertFalse(limiter.allow("ip:1"))
        clock.advance(10)
        self.assertTrue(limiter.allow("ip:1"))

    def test_allowlisted_client_never_blocked(self):
        clock = FakeClock()
        limiter = RateLimiter(limit=1, window_seconds=10, allowlist=["ip:trusted"], clock=clock)
        for _ in range(10):
            self.assertTrue(limiter.allow("ip:trusted"))

    def test_per_client_isolation(self):
        clock = FakeClock()
        limiter = RateLimiter(limit=1, window_seconds=10, clock=clock)
        self.assertTrue(limiter.allow("ip:a"))
        self.assertFalse(limiter.allow("ip:a"))
        self.assertTrue(limiter.allow("ip:b"))

    def test_metrics_track_totals_and_blocks(self):
        clock = FakeClock()
        limiter = RateLimiter(limit=1, window_seconds=10, clock=clock)
        limiter.allow("ip:a")
        limiter.allow("ip:a")
        snapshot = limiter.metrics.snapshot()
        self.assertEqual(snapshot["total_requests"], 2)
        self.assertEqual(snapshot["blocked_requests"], 1)
        self.assertEqual(snapshot["per_client_blocks"]["ip:a"], 1)

    def test_config_update_is_partial_and_live(self):
        limiter = RateLimiter(limit=5, window_seconds=60)
        updated = limiter.update_config(limit=10, window_seconds=30, allowlist=["ip:x"])
        self.assertEqual(updated, {"limit": 10, "window_seconds": 30.0, "allowlist": ["ip:x"]})
        # Partial update leaves untouched fields alone.
        updated2 = limiter.update_config(limit=20)
        self.assertEqual(updated2["window_seconds"], 30.0)
        self.assertEqual(updated2["allowlist"], ["ip:x"])

    def test_reset_specific_key_only(self):
        clock = FakeClock()
        limiter = RateLimiter(limit=1, window_seconds=10, clock=clock)
        limiter.allow("ip:a")
        limiter.allow("ip:b")
        self.assertFalse(limiter.allow("ip:a"))
        self.assertFalse(limiter.allow("ip:b"))
        limiter.reset("ip:a")
        self.assertTrue(limiter.allow("ip:a"))
        self.assertFalse(limiter.allow("ip:b"))

    def test_reset_without_key_clears_everyone(self):
        clock = FakeClock()
        limiter = RateLimiter(limit=1, window_seconds=10, clock=clock)
        limiter.allow("ip:a")
        limiter.allow("ip:b")
        limiter.reset()
        self.assertTrue(limiter.allow("ip:a"))
        self.assertTrue(limiter.allow("ip:b"))

    def test_store_failure_degrades_gracefully(self):
        class BrokenStore(InMemoryRateLimitStore):
            def increment(self, key, window):
                raise RuntimeError("boom")

        limiter = RateLimiter(limit=1, window_seconds=10, store=BrokenStore())
        # A broken storage backend must not take the API down or raise.
        self.assertTrue(limiter.allow("ip:a"))
        self.assertTrue(limiter.allow("ip:a"))

    def test_api_key_identification_takes_priority_over_ip(self):
        environ = {"REMOTE_ADDR": "1.1.1.1", "HTTP_X_API_KEY": "secret"}
        self.assertEqual(RateLimiter.identify(environ), "key:secret")
        environ_no_key = {"REMOTE_ADDR": "1.1.1.1"}
        self.assertEqual(RateLimiter.identify(environ_no_key), "ip:1.1.1.1")


class ApiRateLimitIntegrationTests(unittest.TestCase):
    def _app_with_limit(self, limit=2, window_seconds=60, allowlist=None):
        limiter = RateLimiter(limit=limit, window_seconds=window_seconds, allowlist=allowlist)
        return WSGIApp(limiter=limiter)

    def test_headers_present_on_allowed_request(self):
        app = self._app_with_limit(limit=5)
        status, headers, _ = call(app, "GET", "/nope")
        self.assertEqual(status, 404)
        self.assertIn("X-RateLimit-Limit", headers)
        self.assertIn("X-RateLimit-Remaining", headers)
        self.assertIn("X-RateLimit-Reset", headers)

    def test_returns_429_when_limit_exceeded(self):
        app = self._app_with_limit(limit=1)
        status1, _, _ = call(
            app, "POST", "/api/shorten", json.dumps({"url": "https://example.com/x"})
        )
        self.assertEqual(status1, 201)
        status2, headers2, body2 = call(
            app, "POST", "/api/shorten", json.dumps({"url": "https://example.com/y"})
        )
        self.assertEqual(status2, 429)
        self.assertEqual(headers2["X-RateLimit-Remaining"], "0")
        self.assertIn("error", json.loads(body2))

    def test_redirect_endpoint_is_also_rate_limited(self):
        app = self._app_with_limit(limit=2)
        _, _, body = call(app, "POST", "/api/shorten", json.dumps({"url": "https://example.com/z"}))
        code = json.loads(body)["code"]
        # First request already consumed one unit above; one more should still pass,
        # then the client is throttled.
        status, _, _ = call(app, "GET", "/" + code)
        self.assertEqual(status, 302)
        status, _, _ = call(app, "GET", "/" + code)
        self.assertEqual(status, 429)

    def test_different_clients_have_independent_limits(self):
        app = self._app_with_limit(limit=1)
        status_a, _, _ = call(app, "GET", "/nope", remote_addr="9.9.9.1")
        status_b, _, _ = call(app, "GET", "/nope", remote_addr="9.9.9.2")
        self.assertEqual(status_a, 404)
        self.assertEqual(status_b, 404)

    def test_metrics_endpoint_reports_blocked_requests(self):
        app = self._app_with_limit(limit=1)
        call(app, "GET", "/nope")
        call(app, "GET", "/nope")  # blocked
        status, _, body = call(app, "GET", "/metrics")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertGreaterEqual(payload["blocked_requests"], 1)
        self.assertGreaterEqual(payload["total_requests"], 2)

    def test_admin_get_and_update_config(self):
        app = self._app_with_limit(limit=5, window_seconds=60)
        status, _, body = call(app, "GET", "/admin/rate-limit-config")
        self.assertEqual(status, 200)
        cfg = json.loads(body)
        self.assertEqual(cfg["limit"], 5)

        status, _, body = call(app, "PUT", "/admin/rate-limit-config", json.dumps({"limit": 100}))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["limit"], 100)

        # New threshold takes effect immediately, no redeploy needed.
        for _ in range(50):
            status, _, _ = call(app, "GET", "/nope")
            self.assertEqual(status, 404)

    def test_admin_reset_clears_block(self):
        app = self._app_with_limit(limit=1)
        call(app, "GET", "/nope", remote_addr="5.5.5.5")
        status, _, _ = call(app, "GET", "/nope", remote_addr="5.5.5.5")
        self.assertEqual(status, 429)
        reset_status, _, _ = call(
            app, "POST", "/admin/rate-limit-reset", json.dumps({"key": "ip:5.5.5.5"})
        )
        self.assertEqual(reset_status, 200)
        status, _, _ = call(app, "GET", "/nope", remote_addr="5.5.5.5")
        self.assertEqual(status, 404)

    def test_allowlist_bypasses_limit(self):
        app = self._app_with_limit(limit=1, allowlist=["ip:7.7.7.7"])
        for _ in range(5):
            status, _, _ = call(app, "GET", "/nope", remote_addr="7.7.7.7")
            self.assertEqual(status, 404)

    def test_limiter_none_disables_rate_limiting(self):
        app = WSGIApp(limiter=None)
        for _ in range(10):
            status, headers, _ = call(app, "GET", "/nope")
            self.assertEqual(status, 404)
            self.assertNotIn("X-RateLimit-Limit", headers)

    def test_default_app_uses_config_driven_limiter(self):
        app = WSGIApp()
        self.assertIsNotNone(app.limiter)
        status, headers, _ = call(app, "GET", "/nope")
        self.assertEqual(status, 404)
        self.assertIn("X-RateLimit-Limit", headers)

    def test_healthz_and_admin_paths_are_exempt_from_limiting(self):
        app = self._app_with_limit(limit=1)
        call(app, "GET", "/nope")  # consume the only slot
        status, _, _ = call(app, "GET", "/nope")
        self.assertEqual(status, 429)
        # Exempt paths must still work even though the client is throttled.
        status, _, _ = call(app, "GET", "/healthz")
        self.assertEqual(status, 200)
        status, _, _ = call(app, "GET", "/metrics")
        self.assertEqual(status, 200)
        status, _, _ = call(app, "GET", "/admin/rate-limit-config")
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
