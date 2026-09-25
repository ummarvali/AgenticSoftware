"""End-to-end HTTP API tests."""
import json
import threading
import unittest
from http.client import HTTPConnection

from cardvalidator.server import run_server


class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd, cls.ctx = run_server(host="127.0.0.1", port=0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _conn(self):
        return HTTPConnection("127.0.0.1", self.port, timeout=5)

    def _post(self, path, payload):
        conn = self._conn()
        body = json.dumps(payload)
        conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, data

    def _get(self, path):
        conn = self._conn()
        conn.request("GET", path)
        resp = conn.getresponse()
        data = resp.read().decode("utf-8")
        conn.close()
        return resp.status, data

    def test_health(self):
        status, data = self._get("/healthz")
        self.assertEqual(status, 200)
        self.assertIn("ok", data)

    def test_ready(self):
        status, _ = self._get("/readyz")
        self.assertEqual(status, 200)

    def test_validate_approved(self):
        txn = {
            "transaction_id": "api-tx1",
            "card_number": "4111111111111111",
            "expiry_month": 12,
            "expiry_year": 2099,
            "cvv": "123",
            "amount": 50.0,
            "currency": "USD",
            "merchant_id": "m1",
        }
        status, data = self._post("/v1/validate", txn)
        self.assertEqual(status, 200)
        self.assertEqual(data["decision"], "APPROVED")

    def test_idempotency(self):
        txn = {
            "transaction_id": "api-tx-idem",
            "card_number": "4111111111111111",
            "expiry_month": 12,
            "expiry_year": 2099,
            "cvv": "123",
            "amount": 25.0,
            "currency": "USD",
            "merchant_id": "m1",
        }
        _, data1 = self._post("/v1/validate", txn)
        _, data2 = self._post("/v1/validate", txn)
        self.assertEqual(data1, data2)

    def test_blacklist_then_decline(self):
        status, _ = self._post("/v1/blacklist", {"card_number": "4000000000000002", "reason": "test"})
        self.assertEqual(status, 200)
        txn = {
            "transaction_id": "api-tx-blk",
            "card_number": "4000000000000002",
            "expiry_month": 12,
            "expiry_year": 2099,
            "cvv": "123",
            "amount": 10.0,
        }
        _, data2 = self._post("/v1/validate", txn)
        self.assertIn("BLACKLISTED", data2["reason_codes"])

    def test_rules_endpoint(self):
        status, _ = self._get("/v1/rules")
        self.assertEqual(status, 200)

    def test_metrics_endpoint(self):
        status, _ = self._get("/metrics")
        self.assertEqual(status, 200)

    def test_reload_rules(self):
        status, data = self._post("/v1/rules/reload", {})
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "reloaded")


if __name__ == "__main__":
    unittest.main()
