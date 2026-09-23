"""Integration tests driving the WSGI app end to end (no socket needed)."""

import io
import json
import unittest

from url_shortener.api import WSGIApp


def call(app, method, path, body=None):
    raw = (body or "").encode("utf-8")
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "wsgi.input": io.BytesIO(raw),
        "CONTENT_LENGTH": str(len(raw)),
    }
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    payload = b"".join(app(environ, start_response))
    return int(captured["status"].split()[0]), captured["headers"], payload


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.app = WSGIApp()

    def test_health(self):
        status, _, _ = call(self.app, "GET", "/healthz")
        self.assertEqual(status, 200)

    def test_shorten_then_redirect(self):
        status, _, body = call(
            self.app, "POST", "/api/shorten", json.dumps({"url": "https://example.com/a"})
        )
        self.assertEqual(status, 201)
        code = json.loads(body)["code"]
        status, headers, _ = call(self.app, "GET", "/" + code)
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "https://example.com/a")

    def test_stats(self):
        _, _, body = call(
            self.app, "POST", "/api/shorten", json.dumps({"url": "https://example.com/b"})
        )
        code = json.loads(body)["code"]
        call(self.app, "GET", "/" + code)
        status, _, body = call(self.app, "GET", "/api/stats/" + code)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["clicks"], 1)

    def test_bad_url_returns_400(self):
        status, _, _ = call(
            self.app, "POST", "/api/shorten", json.dumps({"url": "ftp://x"})
        )
        self.assertEqual(status, 400)

    def test_unknown_code_returns_404(self):
        status, _, _ = call(self.app, "GET", "/nope")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
