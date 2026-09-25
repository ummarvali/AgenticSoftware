"""Integration tests: drive the HTTP API end-to-end in-process against a
server bound to an ephemeral localhost port.
"""

import unittest
import threading
import json
import tempfile
import os
import urllib.request
import urllib.error

from urlshortener.server import create_server


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Prevent urllib from transparently following redirects so tests can
    inspect the raw 30x response (status + Location header).

    Overriding `redirect_request` alone is not sufficient: when it
    returns None, urllib treats the 30x response as "unhandled" and
    ultimately raises HTTPError via the default error handler. Overriding
    the `http_error_30x` methods to return the original response avoids
    that and yields the response object directly from `opener.open`.
    """

    def redirect_request(self, *args, **kwargs):
        return None

    def http_error_302(self, req, fp, code, msg, headers):
        return fp

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


class ApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fd, cls.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        cls.httpd, cls.service = create_server(
            host="127.0.0.1", port=0, db_path=cls.db_path,
            base_url="http://127.0.0.1",
        )
        cls.port = cls.httpd.server_address[1]
        cls.base = "http://127.0.0.1:{}".format(cls.port)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        os.remove(cls.db_path)

    def _request(self, method, path, body=None, headers=None):
        url = self.base + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            resp = urllib.request.urlopen(req)
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}"), resp
        except urllib.error.HTTPError as e:
            payload = e.read().decode("utf-8")
            try:
                payload = json.loads(payload)
            except Exception:
                pass
            return e.code, payload, e

    def test_create_url_endpoint(self):
        status, body, _ = self._request(
            "POST", "/api/v1/urls", {"long_url": "https://example.com/foo"}
        )
        self.assertEqual(status, 201)
        self.assertIn("short_code", body)
        self.assertTrue(body["short_url"].endswith(body["short_code"]))

    def test_create_url_invalid_input(self):
        status, body, _ = self._request(
            "POST", "/api/v1/urls", {"long_url": "not-a-url"}
        )
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_redirect_endpoint(self):
        status, body, _ = self._request(
            "POST", "/api/v1/urls",
            {"long_url": "https://example.com/redirect-me", "custom_alias": "redir1"},
        )
        self.assertEqual(status, 201)

        req = urllib.request.Request(self.base + "/api/v1/redir1", method="GET")

        opener = urllib.request.build_opener(NoRedirect)
        resp = opener.open(req)
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.headers.get("Location"), "https://example.com/redirect-me")

    def test_redirect_unknown_code_404(self):
        status, body, _ = self._request("GET", "/api/v1/does-not-exist-code")
        self.assertEqual(status, 404)

    def test_metadata_endpoint(self):
        status, created, _ = self._request(
            "POST", "/api/v1/urls",
            {"long_url": "https://example.com/meta", "custom_alias": "metacode"},
        )
        self.assertEqual(status, 201)
        status, body, _ = self._request("GET", "/api/v1/urls/metacode")
        self.assertEqual(status, 200)
        self.assertEqual(body["long_url"], "https://example.com/meta")
        self.assertEqual(body["click_count"], 0)
        self.assertTrue(body["is_active"])

    def test_metadata_not_found_404(self):
        status, body, _ = self._request("GET", "/api/v1/urls/nope")
        self.assertEqual(status, 404)

    def test_delete_endpoint(self):
        self._request(
            "POST", "/api/v1/urls",
            {"long_url": "https://example.com/del", "custom_alias": "delcode"},
        )
        status, body, _ = self._request("DELETE", "/api/v1/urls/delcode")
        self.assertEqual(status, 200)
        self.assertFalse(body["is_active"])
        status, _, _ = self._request("GET", "/api/v1/delcode")
        self.assertEqual(status, 404)

    def test_delete_not_found_404(self):
        status, body, _ = self._request("DELETE", "/api/v1/urls/nope")
        self.assertEqual(status, 404)

    def test_analytics_endpoint(self):
        self._request(
            "POST", "/api/v1/urls",
            {"long_url": "https://example.com/an", "custom_alias": "anacode"},
        )
        req = urllib.request.Request(self.base + "/api/v1/anacode", method="GET")

        opener = urllib.request.build_opener(NoRedirect)
        opener.open(req)
        opener.open(req)

        status, body, _ = self._request("GET", "/api/v1/urls/anacode/analytics")
        self.assertEqual(status, 200)
        self.assertEqual(body["total_clicks"], 2)
        self.assertEqual(body["short_code"], "anacode")

    def test_analytics_not_found_404(self):
        status, body, _ = self._request("GET", "/api/v1/urls/nope/analytics")
        self.assertEqual(status, 404)

    def test_create_user_endpoint(self):
        status, body, _ = self._request("POST", "/api/v1/users")
        self.assertEqual(status, 201)
        self.assertIn("user_id", body)
        self.assertIn("api_token", body)

    def test_owned_url_delete_requires_auth(self):
        _, user, _ = self._request("POST", "/api/v1/users")
        token = user["api_token"]
        status, created, _ = self._request(
            "POST", "/api/v1/urls",
            {"long_url": "https://example.com/owned", "custom_alias": "ownedcode"},
            headers={"Authorization": "Bearer " + token},
        )
        self.assertEqual(status, 201)

        status, body, _ = self._request("DELETE", "/api/v1/urls/ownedcode")
        self.assertEqual(status, 403)

        status, body, _ = self._request(
            "DELETE", "/api/v1/urls/ownedcode",
            headers={"Authorization": "Bearer " + token},
        )
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()

