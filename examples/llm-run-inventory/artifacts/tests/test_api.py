import json
import unittest
from io import BytesIO

from inventory.api import InventoryAPI


def call(app, method, path, body=None, query=""):
    data = json.dumps(body).encode("utf-8") if body is not None else b""
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "CONTENT_LENGTH": str(len(data)),
        "wsgi.input": BytesIO(data),
    }
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    result = app(environ, start_response)
    response_body = b"".join(result)
    payload = json.loads(response_body.decode("utf-8")) if response_body else {}
    status_code = int(captured["status"].split(" ")[0])
    return status_code, payload


class TestInventoryAPI(unittest.TestCase):
    def setUp(self):
        self.app = InventoryAPI()

    def test_full_flow(self):
        status, product = call(self.app, "POST", "/products",
                                {"sku": "SKU1", "name": "Widget", "description": "A widget"})
        self.assertEqual(status, 201)
        pid = product["id"]

        status, warehouse = call(self.app, "POST", "/warehouses",
                                  {"code": "WH1", "name": "Main", "location": "NY"})
        self.assertEqual(status, 201)
        wid = warehouse["id"]

        status, stock = call(self.app, "POST", "/stock",
                              {"product_id": pid, "warehouse_id": wid, "quantity": 20})
        self.assertEqual(status, 201)
        self.assertEqual(stock["quantity"], 20)

        status, threshold = call(self.app, "PUT", f"/thresholds/{pid}/{wid}", {"min_quantity": 5})
        self.assertEqual(status, 200)
        self.assertEqual(threshold["min_quantity"], 5)

        status, result = call(self.app, "POST", "/stock/adjustments",
                               {"product_id": pid, "warehouse_id": wid, "delta": -18, "reason": "sale"})
        self.assertEqual(status, 201)
        self.assertTrue(result["alert_triggered"])
        self.assertEqual(result["stock_item"]["quantity"], 2)

        status, alerts = call(self.app, "GET", "/alerts")
        self.assertEqual(status, 200)
        self.assertEqual(alerts["total"], 1)
        alert_id = alerts["items"][0]["id"]

        status, resolved = call(self.app, "POST", f"/alerts/{alert_id}/resolve", {"resolved_by": "admin"})
        self.assertEqual(status, 200)
        self.assertEqual(resolved["status"], "RESOLVED")

        status, got_stock = call(self.app, "GET", f"/stock/{wid}/{pid}")
        self.assertEqual(status, 200)
        self.assertEqual(got_stock["quantity"], 2)

        status, listing = call(self.app, "GET", "/stock", query="page=1&page_size=10")
        self.assertEqual(status, 200)
        self.assertEqual(listing["total"], 1)

    def test_get_missing_product_returns_404(self):
        status, payload = call(self.app, "GET", "/products/9999")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
