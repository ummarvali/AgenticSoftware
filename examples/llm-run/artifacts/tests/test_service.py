"""End-to-end tests for the URL shortener service."""
import http.client
import json
import threading
import time
import unittest

from urlshortener import Application, make_server


class TestApplicationLogic(unittest.TestCase):
    def setUp(self):
        self.app = Application(":memory:")

    def tearDown(self):
        self.app.shutdown()

    def test_create_and_resolve_and_analytics(self):
        status, payload = self.app.create_url({"long_url": "https://example.com/very/long/path"})
        self.assertEqual(status, 201)
        code = payload["short_code"]
        self.assertTrue(code)
        self.assertEqual(payload["long_url"], "https://example.com/very/long/path")

        status, info = self.app.get_url_info(code)
        self.assertEqual(status, 200)
        self.assertEqual(info["click_count"], 0)

        resolved = self.app.resolve(code)
        self.assertEqual(resolved, "https://example.com/very/long/path")

        self.app.record_click(code, "https://google.com", "Mozilla/5.0 Mobile")
        self.app.analytics.flush_now()

        status, analytics = self.app.get_analytics(code)
        self.assertEqual(status, 200)
        self.assertEqual(analytics["total_clicks"], 1)
        self.assertEqual(len(analytics["recent_events"]), 1)
        self.assertEqual(analytics["recent_events"][0]["device"], "mobile")

        status, deleted = self.app.delete_url(code)
        self.assertEqual(status, 200)
        self.assertEqual(deleted["status"], "deleted")

        status, _ = self.app.get_url_info(code)
        self.assertEqual(status, 404)

    def test_custom_alias_and_conflict(self):
        status, payload = self.app.create_url(
            {"long_url": "https://example.com/a", "custom_alias": "myalias"}
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["short_code"], "myalias")

        status, payload2 = self.app.create_url(
            {"long_url": "https://example.com/b", "custom_alias": "myalias"}
        )
        self.assertEqual(status, 409)

    def test_invalid_url_rejected(self):
        status, payload = self.app.create_url({"long_url": "not-a-url"})
        self.assertEqual(status, 400)

    def test_expired_url(self):
        status, payload = self.app.create_url(
            {"long_url": "https://example.com/x", "expires_at": "2000-01-01T00:00:00Z"}
        )
        self.assertEqual(status, 201)
        code = payload["short_code"]
        self.assertEqual(self.app.resolve(code), "EXPIRED")


class TestHttpServerIntegration(unittest.TestCase):
    def setUp(self):
        self.server, self.app = make_server(db_path=":memory:", host="127.0.0.1", port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.05)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.shutdown()

    def _conn(self):
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)

    def test_create_redirect_and_analytics_via_http(self):
        conn = self._conn()
        body = json.dumps({"long_url": "https://example.com/hello"})
        conn.request("POST", "/api/urls", body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 201)
        data = json.loads(resp.read())
        code = data["short_code"]
        conn.close()

        conn = self._conn()
        conn.request("GET", f"/{code}")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.getheader("Location"), "https://example.com/hello")
        resp.read()
        conn.close()

        time.sleep(0.3)

        conn = self._conn()
        conn.request("GET", f"/api/urls/{code}/analytics")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        analytics = json.loads(resp.read())
        self.assertEqual(analytics["total_clicks"], 1)
        conn.close()

        conn = self._conn()
        conn.request("DELETE", f"/api/urls/{code}")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        resp.read()
        conn.close()


if __name__ == "__main__":
    unittest.main()
