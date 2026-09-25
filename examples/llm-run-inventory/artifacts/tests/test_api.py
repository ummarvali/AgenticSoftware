"""End to end tests for the inventory service (direct + HTTP layer)."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error

from inventory import db
from inventory.service import InventoryService
from inventory.app import create_server

API_KEY = db.DEFAULT_API_KEY


class ServiceLogicTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db.init_db(self.path)
        self.svc = InventoryService(self.path)

    def tearDown(self):
        os.remove(self.path)

    def test_full_flow(self):
        item = self.svc.create_item("SKU1", "Widget", {"color": "red"})
        wh = self.svc.create_warehouse("WH1", "Main", "NYC")
        added = self.svc.add_stock(item["id"], wh["id"], 50, "tester")
        self.assertEqual(added["quantity"], 50)

        self.svc.set_threshold(item["id"], wh["id"], 20)
        stock = self.svc.get_stock(item["id"], wh["id"])
        self.assertEqual(stock["low_stock_threshold"], 20)

        adj = self.svc.adjust_stock(item["id"], wh["id"], -40, "sale", "tester", "key-1")
        self.assertEqual(adj["quantity"], 10)
        self.assertTrue(adj["alert_triggered"])

        # idempotent replay must not double-apply
        adj2 = self.svc.adjust_stock(item["id"], wh["id"], -40, "sale", "tester", "key-1")
        self.assertEqual(adj2["adjustment_id"], adj["adjustment_id"])
        stock_after = self.svc.get_stock(item["id"], wh["id"])
        self.assertEqual(stock_after["quantity"], 10)

        with self.assertRaises(ValueError):
            self.svc.adjust_stock(item["id"], wh["id"], -1000, "oversell", "tester")

        agg = self.svc.get_stock_by_item(item["id"])
        self.assertEqual(agg["total_quantity"], 10)

        alerts, total = self.svc.list_alerts(1, 10, low_stock_only=True)
        self.assertGreaterEqual(total, 1)

        audit, atotal = self.svc.list_audit(1, 10, item["id"])
        self.assertGreaterEqual(atotal, 2)


class HTTPApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fd, cls.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db.init_db(cls.path)
        cls.server = create_server(cls.path, port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        os.remove(cls.path)

    def _req(self, method, path, body=None, api_key=API_KEY):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if api_key:
            req.add_header("X-API-Key", api_key)
        try:
            resp = urllib.request.urlopen(req)
            return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_openapi_available(self):
        status, payload = self._req("GET", "/openapi.json", api_key=None)
        self.assertEqual(status, 200)
        self.assertIn("paths", payload)

    def test_unauthorized_write(self):
        status, payload = self._req("POST", "/items", {"sku": "X", "name": "Y"}, api_key=None)
        self.assertEqual(status, 401)

    def test_end_to_end(self):
        status, item = self._req("POST", "/items", {"sku": "HTTP-SKU", "name": "Gizmo"})
        self.assertEqual(status, 201)
        status, wh = self._req("POST", "/warehouses", {"code": "W-HTTP", "name": "W1"})
        self.assertEqual(status, 201)

        status, added = self._req("POST", "/stock/add", {
            "item_id": item["id"], "warehouse_id": wh["id"], "quantity": 30, "actor": "tester",
        })
        self.assertEqual(status, 200)
        self.assertEqual(added["quantity"], 30)

        status, thr = self._req(
            "PUT", f"/stock/{item['id']}/{wh['id']}/threshold", {"low_stock_threshold": 25}
        )
        self.assertEqual(status, 200)
        self.assertEqual(thr["low_stock_threshold"], 25)

        status, adj = self._req("POST", "/stock/adjust", {
            "item_id": item["id"], "warehouse_id": wh["id"], "delta": -10,
            "reason": "sale", "actor": "tester",
        })
        self.assertEqual(status, 200)
        self.assertTrue(adj["alert_triggered"])

        status, stock = self._req("GET", f"/stock/{item['id']}/{wh['id']}", api_key=None)
        self.assertEqual(status, 200)
        self.assertEqual(stock["quantity"], 20)

        status, low = self._req("GET", "/alerts/low-stock", api_key=None)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(low["total"], 1)

        status, audit = self._req("GET", "/audit/adjustments", api_key=None)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(audit["total"], 2)


if __name__ == "__main__":
    unittest.main()
