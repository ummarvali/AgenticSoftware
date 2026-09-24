"""End-to-end HTTP API tests using the stdlib http.server based service."""
import unittest
import threading
import json
import urllib.request
import urllib.error
from inventory.server import create_server


class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.service = create_server(db_path=":memory:", port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=2)

    def _req(self, method, path, body=None, auth=True):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if auth:
            req.add_header("Authorization", "Bearer test-key")
        try:
            resp = urllib.request.urlopen(req)
            return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_full_flow(self):
        code, wh = self._req("POST", "/v1/warehouses", {"name": "W1", "location": "LA"})
        self.assertEqual(code, 201)
        code, pr = self._req("POST", "/v1/products", {"name": "P1", "unit": "pcs"})
        self.assertEqual(code, 201)
        code, stock = self._req("POST", "/v1/stock", {
            "warehouse_id": wh["id"], "product_id": pr["id"], "quantity": 50, "threshold": 10,
        })
        self.assertEqual(code, 201)
        self.assertEqual(stock["quantity"], 50)
        code, adj = self._req("POST", "/v1/stock/adjustments", {
            "warehouse_id": wh["id"], "product_id": pr["id"],
            "change_type": "DECREMENT", "amount": 45, "reason": "sale",
        })
        self.assertEqual(code, 201)
        self.assertTrue(adj["alert_triggered"])
        code, alerts = self._req("GET", "/v1/alerts")
        self.assertEqual(code, 200)
        self.assertEqual(len(alerts), 1)
        code, single = self._req("GET", f"/v1/stock/{wh['id']}/{pr['id']}")
        self.assertEqual(single["quantity"], 5)
        code, by_wh = self._req("GET", f"/v1/warehouses/{wh['id']}/stock")
        self.assertEqual(len(by_wh), 1)
        code, by_pr = self._req("GET", f"/v1/products/{pr['id']}/stock")
        self.assertEqual(len(by_pr), 1)
        code, audit = self._req("GET", "/v1/audit")
        self.assertEqual(len(audit), 1)
        code, thr = self._req("PUT", f"/v1/thresholds/{wh['id']}/{pr['id']}", {"threshold": 1})
        self.assertEqual(thr["threshold"], 1)

    def test_unauthorized(self):
        code, body = self._req("GET", "/v1/warehouses", auth=False)
        self.assertEqual(code, 401)

    def test_negative_stock_conflict(self):
        _, wh = self._req("POST", "/v1/warehouses", {"name": "W2", "location": "SF"})
        _, pr = self._req("POST", "/v1/products", {"name": "P2", "unit": "kg"})
        self._req("POST", "/v1/stock", {"warehouse_id": wh["id"], "product_id": pr["id"], "quantity": 5})
        code, body = self._req("POST", "/v1/stock/adjustments", {
            "warehouse_id": wh["id"], "product_id": pr["id"],
            "change_type": "DECREMENT", "amount": 10,
        })
        self.assertEqual(code, 409)


if __name__ == "__main__":
    unittest.main()
