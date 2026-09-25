"""Unit tests for core logic and integration tests driving the HTTP API end to end."""
import http.client
import json
import os
import tempfile
import threading
import unittest

from shortener.storage import Database
from shortener.analytics import AnalyticsQueue, flush
from shortener.service import (
    URLShortenerService,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    base62_encode,
)
from shortener.validation import ValidationError, validate_url
from shortener.server import build_server


class Base62Tests(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(base62_encode(0), "0")

    def test_distinct_codes(self):
        codes = {base62_encode(i) for i in range(1000)}
        self.assertEqual(len(codes), 1000)


class ValidationTests(unittest.TestCase):
    def test_rejects_bad_scheme(self):
        with self.assertRaises(ValidationError):
            validate_url("ftp://example.com/file")

    def test_rejects_blocked_domain(self):
        with self.assertRaises(ValidationError):
            validate_url("http://malware.test/x")

    def test_accepts_valid_url(self):
        self.assertTrue(validate_url("https://example.com/page"))


class ServiceTests(unittest.TestCase):
    """Unit tests for the core service logic, isolated from the HTTP layer."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp()
        os.close(fd)
        self.db = Database(self.path)
        self.queue = AnalyticsQueue()
        self.service = URLShortenerService(self.db, self.queue, base_url="http://short.test")

    def tearDown(self):
        os.remove(self.path)

    def test_create_and_resolve(self):
        result = self.service.create_url("https://example.com/a")
        code = result["short_code"]
        self.assertTrue(code)
        resolved = self.service.resolve(code, referrer="ref", user_agent="ua", ip="127.0.0.1")
        self.assertEqual(resolved, "https://example.com/a")

    def test_custom_alias_conflict(self):
        self.service.create_url("https://example.com/a", custom_alias="mine")
        with self.assertRaises(ConflictError):
            self.service.create_url("https://example.com/b", custom_alias="mine")

    def test_resolve_unknown_returns_none(self):
        self.assertIsNone(self.service.resolve("nope"))

    def test_stats_after_flush(self):
        result = self.service.create_url("https://example.com/a")
        code = result["short_code"]
        self.service.resolve(code, referrer="ref", user_agent="ua", ip="1.2.3.4")
        flush(self.db, self.queue)
        stats = self.service.get_stats(code)
        self.assertEqual(stats["total_clicks"], 1)
        self.assertEqual(len(stats["recent_events"]), 1)

    def test_update_and_delete(self):
        result = self.service.create_url("https://example.com/a", owner_key="k1")
        code = result["short_code"]
        updated = self.service.update_url(code, "k1", long_url="https://example.com/b")
        self.assertEqual(updated["long_url"], "https://example.com/b")
        with self.assertRaises(PermissionDeniedError):
            self.service.update_url(code, "other", long_url="https://example.com/c")
        deleted = self.service.delete_url(code, "k1")
        self.assertTrue(deleted["deleted"])
        with self.assertRaises(NotFoundError):
            self.service.update_url("nosuchcode", "k1")


class ApiIntegrationTests(unittest.TestCase):
    """Drives the real HTTP server end to end, in-process, on an ephemeral localhost port."""

    @classmethod
    def setUpClass(cls):
        fd, cls.db_path = tempfile.mkstemp()
        os.close(fd)
        cls.httpd, cls.stop_event, cls.threads, cls.db, cls.service, cls.queue = build_server(
            "127.0.0.1", 0, cls.db_path, "http://short.test"
        )
        cls.port = cls.httpd.server_address[1]
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.stop_event.set()
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.server_thread.join(timeout=5)
        os.remove(cls.db_path)

    def _request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        hdrs = {"Content-Type": "application/json"}
        hdrs.update(headers or {})
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        if resp.status in (301, 302, 303, 307, 308):
            return resp.status, {"location": resp.getheader("Location")}
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            parsed = {}
        return resp.status, parsed

    def test_create_url(self):
        status, body = self._request("POST", "/api/urls", {"long_url": "https://example.com/hello"})
        self.assertEqual(status, 201)
        self.assertIn("short_code", body)
        self.assertEqual(body["long_url"], "https://example.com/hello")

    def test_create_url_invalid(self):
        status, body = self._request("POST", "/api/urls", {"long_url": "not-a-url"})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_bulk_create(self):
        status, body = self._request(
            "POST",
            "/api/urls/bulk",
            {"urls": [{"long_url": "https://example.com/1"}, {"long_url": "bad"}]},
        )
        self.assertEqual(status, 201)
        self.assertEqual(len(body["results"]), 2)
        self.assertIsNotNone(body["results"][0]["short_code"])
        self.assertIsNone(body["results"][0]["error"])
        self.assertIsNotNone(body["results"][1]["error"])

    def test_redirect(self):
        status, body = self._request("POST", "/api/urls", {"long_url": "https://example.com/redirect-me"})
        code = body["short_code"]
        status, body = self._request("GET", "/" + code)
        self.assertEqual(status, 302)
        self.assertEqual(body["location"], "https://example.com/redirect-me")

    def test_redirect_unknown_returns_404(self):
        status, body = self._request("GET", "/doesnotexist123")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_stats(self):
        status, body = self._request("POST", "/api/urls", {"long_url": "https://example.com/stats-me"})
        code = body["short_code"]
        self.service.resolve(code, referrer="r", user_agent="ua", ip="9.9.9.9")
        flush(self.db, self.queue)
        status, stats_body = self._request("GET", f"/api/urls/{code}/stats")
        self.assertEqual(status, 200)
        self.assertEqual(stats_body["total_clicks"], 1)
        self.assertEqual(len(stats_body["recent_events"]), 1)

    def test_stats_not_found(self):
        status, body = self._request("GET", "/api/urls/doesnotexist/stats")
        self.assertEqual(status, 404)

    def test_update(self):
        status, body = self._request("POST", "/api/urls", {"long_url": "https://example.com/orig"})
        code = body["short_code"]
        status, updated = self._request("PUT", f"/api/urls/{code}", {"long_url": "https://example.com/new"})
        self.assertEqual(status, 200)
        self.assertEqual(updated["long_url"], "https://example.com/new")
        self.assertTrue(updated["updated"])

    def test_update_not_found(self):
        status, body = self._request("PUT", "/api/urls/doesnotexist", {"long_url": "https://example.com/x"})
        self.assertEqual(status, 404)

    def test_update_invalid_body_returns_400(self):
        status, body = self._request("POST", "/api/urls", {"long_url": "https://example.com/tobeupdated"})
        code = body["short_code"]
        status, resp = self._request("PUT", f"/api/urls/{code}", {"long_url": "not-a-url"})
        self.assertEqual(status, 400)

    def test_delete(self):
        status, body = self._request("POST", "/api/urls", {"long_url": "https://example.com/todelete"})
        code = body["short_code"]
        status, deleted = self._request("DELETE", f"/api/urls/{code}")
        self.assertEqual(status, 200)
        self.assertTrue(deleted["deleted"])
        status_redirect, _ = self._request("GET", "/" + code)
        self.assertEqual(status_redirect, 404)

    def test_delete_not_found(self):
        status, body = self._request("DELETE", "/api/urls/doesnotexist")
        self.assertEqual(status, 404)

    def test_create_key(self):
        status, body = self._request("POST", "/api/keys", {"owner_name": "alice"})
        self.assertEqual(status, 201)
        self.assertIn("api_key", body)
        self.assertEqual(body["owner_name"], "alice")

    def test_create_key_missing_owner_name(self):
        status, body = self._request("POST", "/api/keys", {})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
