"""Integration tests: drive the HTTP API end to end against a real
server bound to an ephemeral localhost port, using only urllib.
"""
import json
import threading
import unittest
import urllib.error
import urllib.request

from inventory.server import build_server


class ApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = build_server("127.0.0.1", 0, ":memory:")
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def _req(self, method, path, body=None, key="demo-admin-key"):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if key:
            req.add_header("X-Api-Key", key)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            return e.code, (json.loads(raw) if raw else None)

    # ------------------------------------------------------------- happy path
    def test_full_flow(self):
        status, wh = self._req("POST", "/warehouses", {"name": "Central", "location": "NYC"})
        self.assertEqual(status, 201)
        wid = wh["id"]

        status, listed = self._req("GET", "/warehouses", key="demo-read-key")
        self.assertEqual(status, 200)
        self.assertTrue(any(w["id"] == wid for w in listed))

        status, got = self._req("GET", f"/warehouses/{wid}", key="demo-read-key")
        self.assertEqual(status, 200)
        self.assertEqual(got["id"], wid)

        status, prod = self._req("POST", "/products", {"sku": "SKU-100", "name": "Gadget", "description": "d"})
        self.assertEqual(status, 201)
        pid = prod["id"]

        status, plist = self._req("GET", "/products", key="demo-read-key")
        self.assertEqual(status, 200)
        self.assertTrue(any(p["id"] == pid for p in plist))

        status, pgot = self._req("GET", f"/products/{pid}", key="demo-read-key")
        self.assertEqual(status, 200)
        self.assertEqual(pgot["sku"], "SKU-100")

        status, thr = self._req("PUT", f"/thresholds/{wid}/{pid}", {"threshold": 5})
        self.assertEqual(status, 200)
        self.assertEqual(thr["threshold"], 5)

        status, deft = self._req("PUT", "/thresholds/default", {"default_threshold": 2})
        self.assertEqual(status, 200)
        self.assertEqual(deft["default_threshold"], 2)

        status, added = self._req("POST", "/stock", {"warehouse_id": wid, "product_id": pid, "quantity": 10})
        self.assertEqual(status, 201)
        self.assertEqual(added["quantity"], 10)

        status, one = self._req("GET", f"/stock/{wid}/{pid}", key="demo-read-key")
        self.assertEqual(status, 200)
        self.assertEqual(one["quantity"], 10)

        status, by_prod = self._req("GET", f"/products/{pid}/stock", key="demo-read-key")
        self.assertEqual(status, 200)
        self.assertEqual(by_prod[0]["quantity"], 10)

        status, by_wh = self._req("GET", f"/warehouses/{wid}/stock", key="demo-read-key")
        self.assertEqual(status, 200)
        self.assertEqual(by_wh[0]["quantity"], 10)

        status, adjusted = self._req("POST", "/stock/adjust",
                                      {"warehouse_id": wid, "product_id": pid, "delta": -8, "reason": "sale"})
        self.assertEqual(status, 200)
        self.assertEqual(adjusted["quantity"], 2)
        self.assertTrue(adjusted["alert_triggered"])

        status, alerts = self._req("GET", "/alerts?status=ACTIVE", key="demo-read-key")
        self.assertEqual(status, 200)
        self.assertEqual(len(alerts), 1)
        alert_id = alerts[0]["id"]

        status, resolved = self._req("POST", f"/alerts/{alert_id}/resolve", {})
        self.assertEqual(status, 200)
        self.assertEqual(resolved["status"], "RESOLVED")

        status, history = self._req("GET", f"/stock/{wid}/{pid}/history", key="demo-read-key")
        self.assertEqual(status, 200)
        self.assertEqual(len(history), 2)

        req = urllib.request.Request(self.base + "/metrics", method="GET")
        with urllib.request.urlopen(req) as resp:
            text = resp.read().decode()
        self.assertIn("requests_total", text)

    def test_idempotent_adjust_is_safe_on_retry(self):
        _, wh = self._req("POST", "/warehouses", {"name": "W2", "location": "X"})
        _, prod = self._req("POST", "/products", {"sku": "SKU-IDEM", "name": "N", "description": ""})
        wid, pid = wh["id"], prod["id"]
        self._req("POST", "/stock", {"warehouse_id": wid, "product_id": pid, "quantity": 10})
        body = {"warehouse_id": wid, "product_id": pid, "delta": -3}
        req1 = urllib.request.Request(self.base + "/stock/adjust", data=json.dumps(body).encode(), method="POST")
        req1.add_header("X-Api-Key", "demo-admin-key")
        req1.add_header("Idempotency-Key", "abc-123")
        with urllib.request.urlopen(req1) as resp:
            first = json.loads(resp.read().decode())
        req2 = urllib.request.Request(self.base + "/stock/adjust", data=json.dumps(body).encode(), method="POST")
        req2.add_header("X-Api-Key", "demo-admin-key")
        req2.add_header("Idempotency-Key", "abc-123")
        with urllib.request.urlopen(req2) as resp:
            second = json.loads(resp.read().decode())
        self.assertEqual(first, second)
        status, current = self._req("GET", f"/stock/{wid}/{pid}", key="demo-read-key")
        self.assertEqual(current["quantity"], 7)  # not decremented twice

    # ------------------------------------------------------------- errors
    def test_unknown_warehouse_returns_404(self):
        status, body = self._req("GET", "/warehouses/does-not-exist", key="demo-read-key")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_invalid_quantity_returns_400(self):
        _, wh = self._req("POST", "/warehouses", {"name": "W3", "location": "Y"})
        _, prod = self._req("POST", "/products", {"sku": "SKU-BAD", "name": "N", "description": ""})
        status, body = self._req("POST", "/stock",
                                  {"warehouse_id": wh["id"], "product_id": prod["id"], "quantity": -5})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_missing_api_key_returns_401(self):
        status, _ = self._req("GET", "/warehouses", key=None)
        self.assertEqual(status, 401)

    def test_read_only_key_forbidden_on_write_returns_403(self):
        status, _ = self._req("POST", "/warehouses", {"name": "W4", "location": "Z"}, key="demo-read-key")
        self.assertEqual(status, 403)

    def test_unknown_route_returns_404(self):
        status, _ = self._req("GET", "/nope", key="demo-read-key")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
