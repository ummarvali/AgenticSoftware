"""Unit tests for core logic and integration tests exercising the HTTP API
end to end over real localhost sockets (no sleeps, no external network)."""
import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest

from urlshortener import server as server_mod
from urlshortener.cache import LRUCache
from urlshortener.config import Config
from urlshortener.ratelimit import RateLimiter
from urlshortener.service import ExpiredError, NotFoundError, URLService
from urlshortener.shortcode import generate_short_code
from urlshortener.storage import Storage
from urlshortener.validator import ValidationError, validate_custom_alias, validate_long_url


def _make_config(tmpdir, **overrides):
    cfg = Config()
    cfg.db_path = os.path.join(tmpdir, "test.db")
    cfg.host = "127.0.0.1"
    cfg.port = 0
    cfg.sweeper_interval_seconds = 3600
    cfg.anonymous_rate_limit_per_min = 1000
    cfg.key_creation_rate_limit_per_min = 1000
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cfg = _make_config(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_valid_url_accepted(self):
        self.assertEqual(
            validate_long_url("https://example.com/page", self.cfg),
            "https://example.com/page",
        )

    def test_bad_scheme_rejected(self):
        with self.assertRaises(ValidationError):
            validate_long_url("ftp://example.com/file", self.cfg)

    def test_localhost_denied(self):
        with self.assertRaises(ValidationError):
            validate_long_url("http://localhost/secret", self.cfg)

    def test_private_ip_denied(self):
        with self.assertRaises(ValidationError):
            validate_long_url("http://127.0.0.1/admin", self.cfg)

    def test_too_long_rejected(self):
        long_url = "https://example.com/" + ("a" * 3000)
        with self.assertRaises(ValidationError):
            validate_long_url(long_url, self.cfg)

    def test_custom_alias_format(self):
        self.assertEqual(validate_custom_alias("my-alias_1"), "my-alias_1")
        with self.assertRaises(ValidationError):
            validate_custom_alias("a")
        with self.assertRaises(ValidationError):
            validate_custom_alias("bad alias!")


class ShortCodeTests(unittest.TestCase):
    def test_length_and_alphabet(self):
        code = generate_short_code(7)
        self.assertEqual(len(code), 7)
        self.assertTrue(all(c.isalnum() for c in code))

    def test_randomness(self):
        codes = {generate_short_code(9) for _ in range(50)}
        self.assertGreater(len(codes), 45)


class RateLimiterTests(unittest.TestCase):
    def test_allow_then_deny(self):
        limiter = RateLimiter()
        self.assertTrue(limiter.allow("k", 1))
        self.assertFalse(limiter.allow("k", 1))


class URLServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cfg = _make_config(self.tmpdir)
        self.storage = Storage(self.cfg.db_path)
        self.cache = LRUCache(self.cfg.cache_capacity)
        self.service = URLService(self.cfg, self.storage, self.cache)

    def tearDown(self):
        self.service.shutdown()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_and_resolve(self):
        result = self.service.create_short_url("https://example.com/one")
        long_url = self.service.resolve(result["short_code"])
        self.assertEqual(long_url, "https://example.com/one")

    def test_duplicate_submission_reused(self):
        first = self.service.create_short_url("https://example.com/dup")
        second = self.service.create_short_url("https://example.com/dup")
        self.assertEqual(first["short_code"], second["short_code"])
        self.assertTrue(second["reused_existing"])

    def test_custom_alias_conflict(self):
        self.service.create_short_url("https://example.com/a", custom_alias="mycode")
        with self.assertRaises(ValidationError):
            self.service.create_short_url("https://example.com/b", custom_alias="mycode")

    def test_expired_url(self):
        result = self.service.create_short_url("https://example.com/exp", ttl_seconds=-5)
        with self.assertRaises(ExpiredError):
            self.service.resolve(result["short_code"])

    def test_not_found(self):
        with self.assertRaises(NotFoundError):
            self.service.resolve("doesnotexist")

    def test_analytics_recorded(self):
        result = self.service.create_short_url("https://example.com/track")
        self.service.resolve(
            result["short_code"], referrer="https://ref.example", user_agent="pytest"
        )
        self.service.flush_analytics()
        data = self.service.get_analytics(result["short_code"])
        self.assertEqual(data["total_clicks"], 1)
        self.assertIn("https://ref.example", data["clicks_by_referrer"])


class ApiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.cfg = _make_config(cls.tmpdir)
        cls.httpd, cls.service = server_mod.build_server(cls.cfg)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.service.shutdown()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _conn(self):
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)

    def _post(self, path, payload, headers=None):
        conn = self._conn()
        body = json.dumps(payload).encode("utf-8")
        hdrs = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        if headers:
            hdrs.update(headers)
        conn.request("POST", path, body=body, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, (json.loads(data) if data else {})

    def _get(self, path):
        conn = self._conn()
        conn.request("GET", path)
        resp = conn.getresponse()
        data = resp.read()
        headers = dict(resp.getheaders())
        conn.close()
        parsed = None
        if data and resp.getheader("Content-Type", "").startswith("application/json"):
            parsed = json.loads(data)
        return resp.status, headers, parsed

    def test_01_create_url(self):
        status, body = self._post("/api/v1/urls", {"long_url": "https://example.com/foo"})
        self.assertEqual(status, 201)
        self.assertIn("short_code", body)
        self.assertIn("short_url", body)
        self.assertEqual(body["long_url"], "https://example.com/foo")
        self.assertFalse(body["reused_existing"])

    def test_02_redirect(self):
        _, body = self._post("/api/v1/urls", {"long_url": "https://example.com/bar"})
        status, headers, _ = self._get(f"/{body['short_code']}")
        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "https://example.com/bar")

    def test_03_get_url_info(self):
        _, body = self._post("/api/v1/urls", {"long_url": "https://example.com/info"})
        status, _, info = self._get(f"/api/v1/urls/{body['short_code']}")
        self.assertEqual(status, 200)
        self.assertEqual(info["long_url"], "https://example.com/info")
        self.assertEqual(info["click_count"], 0)

    def test_04_analytics(self):
        _, body = self._post("/api/v1/urls", {"long_url": "https://example.com/analytics"})
        code = body["short_code"]
        self._get(f"/{code}")
        self.service.flush_analytics()
        status, _, data = self._get(f"/api/v1/urls/{code}/analytics")
        self.assertEqual(status, 200)
        self.assertEqual(data["total_clicks"], 1)
        self.assertEqual(len(data["recent_events"]), 1)

    def test_05_create_key(self):
        status, body = self._post("/api/v1/keys", {})
        self.assertEqual(status, 201)
        self.assertIn("api_key", body)
        self.assertIn("rate_limit_per_min", body)

    def test_06_healthz(self):
        status, _, body = self._get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_07_metrics(self):
        conn = self._conn()
        conn.request("GET", "/metrics")
        resp = conn.getresponse()
        data = resp.read().decode()
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertIn("urlshortener_requests_total", data)

    def test_08_invalid_input_missing_long_url(self):
        status, body = self._post("/api/v1/urls", {})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_09_invalid_input_bad_scheme(self):
        status, _ = self._post("/api/v1/urls", {"long_url": "javascript:alert(1)"})
        self.assertEqual(status, 400)

    def test_10_unknown_short_code(self):
        status, _, body = self._get("/doesnotexist12345")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not_found")

    def test_11_unknown_api_path(self):
        status, _, _ = self._get("/api/v1/urls/doesnotexist12345")
        self.assertEqual(status, 404)

    def test_12_expired_redirect(self):
        _, body = self._post(
            "/api/v1/urls", {"long_url": "https://example.com/gone", "ttl_seconds": -10}
        )
        status, _, resp_body = self._get(f"/{body['short_code']}")
        self.assertEqual(status, 410)
        self.assertEqual(resp_body["error"], "expired")

    def test_13_openapi_served(self):
        status, _, body = self._get("/openapi.json")
        self.assertEqual(status, 200)
        self.assertIn("paths", body)


class RateLimitTests(unittest.TestCase):
    def test_create_rate_limited(self):
        tmpdir = tempfile.mkdtemp()
        try:
            cfg = _make_config(tmpdir, anonymous_rate_limit_per_min=1)
            httpd, service = server_mod.build_server(cfg)
            port = httpd.server_address[1]
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                statuses = []
                for _ in range(2):
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    payload = json.dumps({"long_url": "https://example.com/rl"}).encode()
                    conn.request(
                        "POST",
                        "/api/v1/urls",
                        body=payload,
                        headers={
                            "Content-Type": "application/json",
                            "Content-Length": str(len(payload)),
                        },
                    )
                    resp = conn.getresponse()
                    statuses.append(resp.status)
                    resp.read()
                    conn.close()
                self.assertIn(429, statuses)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)
                service.shutdown()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
