"""End-to-end tests for the URL shortener WSGI API and service layer."""
import io
import json
import unittest

from urlshortener.app import app as wsgi_app
from urlshortener import URLService


def call(app, method, path, body=None, headers=None):
    env = {
        "REQUEST_METHOD": method, "PATH_INFO": path, "SERVER_NAME": "test",
        "SERVER_PORT": "80", "wsgi.input": io.BytesIO(b""), "wsgi.errors": io.StringIO(),
        "wsgi.version": (1, 0), "wsgi.multithread": False, "wsgi.multiprocess": False,
        "wsgi.run_once": False, "wsgi.url_scheme": "http",
    }
    if body is not None:
        raw = json.dumps(body).encode()
        env["wsgi.input"] = io.BytesIO(raw)
        env["CONTENT_LENGTH"] = str(len(raw))
    if headers:
        env.update(headers)
    result = {}

    def start_response(status, hdrs):
        result["status"] = status
        result["headers"] = dict(hdrs)

    resp = app(env, start_response)
    body_bytes = b"".join(resp)
    return result["status"], result["headers"], body_bytes


class TestUrlShortenerFlow(unittest.TestCase):
    def test_full_flow(self):
        status, _, body = call(wsgi_app, "POST", "/api/v1/urls", {"long_url": "https://example.com"})
        self.assertTrue(status.startswith("201"))
        data = json.loads(body)
        code = data["short_code"]
        self.assertEqual(data["long_url"], "https://example.com")

        status, headers, _ = call(wsgi_app, "GET", f"/{code}")
        self.assertTrue(status.startswith("302"))
        self.assertEqual(headers["Location"], "https://example.com")

        status, _, body = call(wsgi_app, "GET", f"/api/v1/urls/{code}")
        self.assertTrue(status.startswith("200"))
        data = json.loads(body)
        self.assertEqual(data["click_count"], 1)

        status, _, body = call(wsgi_app, "PUT", f"/api/v1/urls/{code}", {"long_url": "https://updated.com"})
        self.assertTrue(status.startswith("200"))
        data = json.loads(body)
        self.assertEqual(data["long_url"], "https://updated.com")

        status, _, body = call(wsgi_app, "GET", f"/api/v1/urls/{code}/analytics")
        self.assertTrue(status.startswith("200"))
        data = json.loads(body)
        self.assertEqual(data["short_code"], code)
        self.assertGreaterEqual(data["total_clicks"], 1)

        status, _, body = call(wsgi_app, "DELETE", f"/api/v1/urls/{code}")
        self.assertTrue(status.startswith("200"))
        data = json.loads(body)
        self.assertTrue(data["deleted"])

        status, _, body = call(wsgi_app, "GET", f"/api/v1/urls/{code}")
        self.assertTrue(status.startswith("404"))

    def test_health(self):
        status, _, body = call(wsgi_app, "GET", "/api/v1/health")
        self.assertTrue(status.startswith("200"))
        data = json.loads(body)
        self.assertEqual(data["status"], "ok")

    def test_missing_long_url(self):
        status, _, _ = call(wsgi_app, "POST", "/api/v1/urls", {})
        self.assertTrue(status.startswith("400"))


class TestServiceUnit(unittest.TestCase):
    def test_create_and_custom_alias_conflict(self):
        svc = URLService()
        m = svc.create_url("https://a.com", custom_alias="my-alias")
        self.assertEqual(m.short_code, "my-alias")
        with self.assertRaises(ValueError):
            svc.create_url("https://b.com", custom_alias="my-alias")

    def test_redirect_inactive_url(self):
        svc = URLService()
        m = svc.create_url("https://a.com")
        svc.update_url(m.short_code, is_active=False)
        self.assertIsNone(svc.redirect(m.short_code))


if __name__ == "__main__":
    unittest.main()
