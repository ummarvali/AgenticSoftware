"""Unit and integration tests for the URL shortener service.

Integration tests spin up the real WSGI app on an ephemeral localhost
port (port 0) in a background thread and issue HTTP requests via
urllib; no sleeps are used and everything is torn down per test.
"""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from urlshortener.app import Application
from urlshortener.config import Config
from urlshortener.db import Database, encode_base62
from urlshortener.security import RateLimiter, RateLimitError
from urlshortener.server import ThreadingWSGIServer
from urlshortener import validator
from wsgiref.simple_server import make_server


# ---------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------
class ValidatorTests(unittest.TestCase):
    def test_valid_http_url(self):
        self.assertTrue(validator.validate_url("https://example.com/x"))

    def test_rejects_bad_scheme(self):
        with self.assertRaises(ValueError):
            validator.validate_url("ftp://example.com/x")

    def test_rejects_javascript(self):
        with self.assertRaises(ValueError):
            validator.validate_url("javascript:alert(1)")

    def test_rejects_private_ip(self):
        with self.assertRaises(ValueError):
            validator.validate_url("http://127.0.0.1/admin")

    def test_rejects_too_long(self):
        with self.assertRaises(ValueError):
            validator.validate_url("http://example.com/" + "a" * 3000)

    def test_alias_validation(self):
        validator.validate_alias("my-alias_1")
        with self.assertRaises(ValueError):
            validator.validate_alias("ab")
        with self.assertRaises(ValueError):
            validator.validate_alias("api")


class ShortCodeTests(unittest.TestCase):
    def test_encoding_unique_and_deterministic(self):
        seen = set()
        for n in range(0, 500):
            code = encode_base62(n)
            self.assertNotIn(code, seen)
            seen.add(code)
        self.assertEqual(encode_base62(0), "0")


class RateLimiterTests(unittest.TestCase):
    def test_denies_after_capacity(self):
        limiter = RateLimiter(rate_per_minute=2)
        limiter.check("peer-1")
        limiter.check("peer-1")
        with self.assertRaises(RateLimitError):
            limiter.check("peer-1")

    def test_independent_per_identifier(self):
        limiter = RateLimiter(rate_per_minute=1)
        limiter.check("a")
        limiter.check("b")  # different identifier, own bucket


# ---------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------
class IntegrationTestBase(unittest.TestCase):
    API_KEY = "testkey"

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        cfg = Config()
        cfg.db_path = self.db_path
        cfg.api_keys = f"{self.API_KEY}:tester"
        cfg.host = "127.0.0.1"
        cfg.port = 0
        cfg.rate_limit_per_minute = 1000
        cfg.redirect_rate_limit_per_minute = 1000
        self.config = cfg

        self.db = Database(cfg.db_path)
        self.db.seed_api_keys(cfg.api_keys)
        self.app = Application(self.db, cfg)

        self.httpd = make_server(cfg.host, cfg.port, self.app, server_class=ThreadingWSGIServer)
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def request(self, method, path, body=None, headers=None, api_key=None):
        url = self.base_url + path
        data = None
        hdrs = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        if api_key is not None:
            hdrs["X-API-Key"] = api_key
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            resp = urllib.request.urlopen(req)
            payload = resp.read()
            return resp.status, dict(resp.getheaders()), payload
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers.items()), exc.read()


class HealthEndpointTest(IntegrationTestBase):
    def test_health(self):
        status, _headers, body = self.request("GET", "/healthz")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["status"], "ok")


class CreateUrlEndpointTest(IntegrationTestBase):
    def test_create_success(self):
        status, _h, body = self.request(
            "POST", "/api/v1/urls", body={"long_url": "https://example.com/a"}, api_key=self.API_KEY
        )
        self.assertEqual(status, 201)
        data = json.loads(body)
        self.assertIn("short_code", data)
        self.assertEqual(data["long_url"], "https://example.com/a")
        self.assertIsNone(data["expires_at"])

    def test_create_missing_auth(self):
        status, _h, _b = self.request("POST", "/api/v1/urls", body={"long_url": "https://example.com/a"})
        self.assertEqual(status, 401)

    def test_create_invalid_url(self):
        status, _h, body = self.request(
            "POST", "/api/v1/urls", body={"long_url": "ftp://bad"}, api_key=self.API_KEY
        )
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(body))

    def test_create_custom_alias_conflict(self):
        status1, _h, _b = self.request(
            "POST",
            "/api/v1/urls",
            body={"long_url": "https://example.com/a", "custom_alias": "myalias"},
            api_key=self.API_KEY,
        )
        self.assertEqual(status1, 201)
        status2, _h, body2 = self.request(
            "POST",
            "/api/v1/urls",
            body={"long_url": "https://example.com/b", "custom_alias": "myalias"},
            api_key=self.API_KEY,
        )
        self.assertEqual(status2, 409)

    def test_create_with_ttl(self):
        status, _h, body = self.request(
            "POST",
            "/api/v1/urls",
            body={"long_url": "https://example.com/ttl", "ttl_seconds": 3600},
            api_key=self.API_KEY,
        )
        self.assertEqual(status, 201)
        self.assertIsNotNone(json.loads(body)["expires_at"])


class RedirectEndpointTest(IntegrationTestBase):
    def _create(self, long_url="https://example.com/redirect-target"):
        status, _h, body = self.request(
            "POST", "/api/v1/urls", body={"long_url": long_url}, api_key=self.API_KEY
        )
        self.assertEqual(status, 201)
        return json.loads(body)["short_code"]

    def test_redirect_success(self):
        code = self._create()
        req = urllib.request.Request(self.base_url + "/" + code, method="GET")
        try:
            urllib.request.urlopen(req)
            self.fail("expected redirect, not a 200 fetch")
        except urllib.error.HTTPError as exc:
            # urlopen follows redirects by default for GET; force no-follow
            pass
        opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler)

        class NoRedirect(urllib.request.HTTPErrorProcessor):
            def http_response(self, request, response):
                return response

        no_redirect_opener = urllib.request.build_opener(NoRedirect)
        resp = no_redirect_opener.open(self.base_url + "/" + code)
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.headers.get("Location"), "https://example.com/redirect-target")

    def test_redirect_unknown(self):
        status, _h, _b = self.request("GET", "/doesnotexist")
        self.assertEqual(status, 404)


class MetadataEndpointTest(IntegrationTestBase):
    def _create(self):
        status, _h, body = self.request(
            "POST", "/api/v1/urls", body={"long_url": "https://example.com/meta"}, api_key=self.API_KEY
        )
        return json.loads(body)["short_code"]

    def test_get_metadata(self):
        code = self._create()
        status, _h, body = self.request("GET", f"/api/v1/urls/{code}", api_key=self.API_KEY)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["short_code"], code)
        self.assertTrue(data["is_active"])
        self.assertEqual(data["owner"], "tester")

    def test_get_metadata_unknown(self):
        status, _h, _b = self.request("GET", "/api/v1/urls/doesnotexist", api_key=self.API_KEY)
        self.assertEqual(status, 404)

    def test_get_metadata_no_auth(self):
        code = self._create()
        status, _h, _b = self.request("GET", f"/api/v1/urls/{code}")
        self.assertEqual(status, 401)


class DeleteEndpointTest(IntegrationTestBase):
    def _create(self):
        status, _h, body = self.request(
            "POST", "/api/v1/urls", body={"long_url": "https://example.com/del"}, api_key=self.API_KEY
        )
        return json.loads(body)["short_code"]

    def test_delete_success(self):
        code = self._create()
        status, _h, body = self.request("DELETE", f"/api/v1/urls/{code}", api_key=self.API_KEY)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertFalse(data["is_active"])
        status2, _h, _b = self.request("GET", "/" + code)
        self.assertEqual(status2, 404)

    def test_delete_wrong_owner(self):
        code = self._create()
        self.db.seed_api_keys("otherkey:bob")
        status, _h, _b = self.request("DELETE", f"/api/v1/urls/{code}", api_key="otherkey")
        self.assertEqual(status, 403)

    def test_delete_unknown(self):
        status, _h, _b = self.request("DELETE", "/api/v1/urls/doesnotexist", api_key=self.API_KEY)
        self.assertEqual(status, 404)


class AnalyticsEndpointTest(IntegrationTestBase):
    def test_analytics_after_clicks(self):
        status, _h, body = self.request(
            "POST", "/api/v1/urls", body={"long_url": "https://example.com/stats"}, api_key=self.API_KEY
        )
        code = json.loads(body)["short_code"]

        class NoRedirect(urllib.request.HTTPErrorProcessor):
            def http_response(self, request, response):
                return response

        opener = urllib.request.build_opener(NoRedirect)
        for _ in range(3):
            opener.open(self.base_url + "/" + code)

        status, _h, body = self.request("GET", f"/api/v1/urls/{code}/analytics", api_key=self.API_KEY)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["total_clicks"], 3)
        self.assertEqual(len(data["clicks_by_day"]), 1)

    def test_analytics_unknown(self):
        status, _h, _b = self.request("GET", "/api/v1/urls/doesnotexist/analytics", api_key=self.API_KEY)
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
