"""End-to-end tests for the URL shortener HTTP service."""
import http.client
import json
import threading
import time
import unittest

from urlshortener.db import Database
from urlshortener.server import build_server
from urlshortener import shortcode, validation


class ServiceTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = Database(":memory:")
        cls.httpd = build_server(cls.db, port=0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.stop_sweeper.set()
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)

    def _conn(self):
        return http.client.HTTPConnection("localhost", self.port, timeout=5)

    def _post(self, path, body, headers=None):
        conn = self._conn()
        conn.request("POST", path, body=json.dumps(body),
                      headers=headers or {"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        return resp.status, data

    def _get(self, path, headers=None):
        conn = self._conn()
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp, body

    def test_full_flow_create_redirect_metadata_analytics(self):
        status, data = self._post("/api/v1/urls", {"long_url": "https://example.com/page"})
        self.assertEqual(status, 201)
        code = data["short_code"]
        self.assertTrue(code)
        self.assertEqual(data["long_url"], "https://example.com/page")

        resp, _ = self._get(f"/{code}")
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.getheader("Location"), "https://example.com/page")

        resp2, body2 = self._get(f"/api/v1/urls/{code}")
        meta = json.loads(body2)
        self.assertEqual(resp2.status, 200)
        self.assertEqual(meta["click_count"], 1)
        self.assertTrue(meta["is_active"])

        resp3, body3 = self._get(f"/api/v1/urls/{code}/analytics")
        analytics = json.loads(body3)
        self.assertEqual(resp3.status, 200)
        self.assertEqual(analytics["total_clicks"], 1)
        self.assertEqual(len(analytics["clicks_by_day"]), 1)
        self.assertEqual(len(analytics["top_referrers"]), 1)

    def test_invalid_url_rejected(self):
        status, data = self._post("/api/v1/urls", {"long_url": "javascript:alert(1)"})
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_custom_alias_and_auth_protected_delete(self):
        status, data = self._post("/api/v1/auth/register", {"username": "alice", "password": "secret"})
        self.assertEqual(status, 201)
        api_key = data["api_key"]
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

        status, data = self._post(
            "/api/v1/urls",
            {"long_url": "https://example.org", "custom_alias": "myalias1"},
            headers=headers,
        )
        self.assertEqual(status, 201)
        self.assertEqual(data["short_code"], "myalias1")

        conn = self._conn()
        conn.request("DELETE", "/api/v1/urls/myalias1")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        self.assertEqual(resp.status, 403)

        conn = self._conn()
        conn.request("DELETE", "/api/v1/urls/myalias1", headers=headers)
        resp = conn.getresponse()
        body = json.loads(resp.read())
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertFalse(body["is_active"])

    def test_login_and_not_found(self):
        self._post("/api/v1/auth/register", {"username": "bob", "password": "pw"})
        status, data = self._post("/api/v1/auth/login", {"username": "bob", "password": "pw"})
        self.assertEqual(status, 200)
        self.assertIn("api_key", data)

        resp, _ = self._get("/doesnotexist123")
        self.assertEqual(resp.status, 404)

    def test_openapi_and_metrics(self):
        resp, body = self._get("/openapi.json")
        self.assertEqual(resp.status, 200)
        self.assertIn("openapi", json.loads(body))
        resp2, _ = self._get("/metrics")
        self.assertEqual(resp2.status, 200)


class UnitTestCase(unittest.TestCase):
    def test_shortcode_encode_roundtrip_distinctness(self):
        codes = {shortcode.encode(i) for i in range(0, 500)}
        self.assertEqual(len(codes), 500)

    def test_validate_alias(self):
        self.assertTrue(shortcode.validate_alias("abc-123"))
        self.assertFalse(shortcode.validate_alias("ab"))
        self.assertFalse(shortcode.validate_alias("bad!alias"))

    def test_validate_url(self):
        self.assertEqual(validation.validate_url("https://a.com"), "https://a.com")
        with self.assertRaises(validation.ValidationError):
            validation.validate_url("ftp://a.com")


if __name__ == "__main__":
    unittest.main()
