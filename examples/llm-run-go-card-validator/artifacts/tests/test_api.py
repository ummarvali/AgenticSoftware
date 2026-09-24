"""End to end tests for the WSGI API via direct in-process invocation."""
import io
import json
import os
import tempfile
import unittest

from cardvalidator import create_app, Config, Storage
from cardvalidator import validation


def call(app, method, path, payload=None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    }
    result = {}

    def start_response(status, headers):
        result["status"] = status
        result["headers"] = headers

    body_iter = app(environ, start_response)
    data = b"".join(body_iter)
    parsed = json.loads(data) if data else None
    return result["status"], parsed


class ApiTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.config = Config(
            allowed_currencies={"USD", "EUR"},
            min_amount_cents=1, max_amount_cents=1000000,
            daily_limit_cents=5000, monthly_limit_cents=10000,
            idempotency_ttl_seconds=3600, db_path=self.db_path,
        )
        self.storage = Storage(self.db_path)
        self.app = create_app(self.config, self.storage)
        self.valid_payload = {
            "card_number": "4111111111111111", "cvv": "123",
            "expiry_month": 12, "expiry_year": 2099,
            "amount_cents": 1000, "currency": "USD",
        }

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_healthz(self):
        status, body = call(self.app, "GET", "/healthz")
        self.assertEqual(status, "200 OK")
        self.assertEqual(body["status"], "ok")

    def test_readyz(self):
        status, body = call(self.app, "GET", "/readyz")
        self.assertEqual(status, "200 OK")
        self.assertTrue(body["checks"]["sqlite"])

    def test_valid_transaction_approved(self):
        status, body = call(self.app, "POST", "/v1/transactions/validate", self.valid_payload)
        self.assertEqual(status, "200 OK")
        self.assertEqual(body["status"], "approved")
        self.assertEqual(body["card_network"], "visa")
        self.assertEqual(body["masked_pan"], "411111*******1111")

    def test_invalid_luhn_rejected(self):
        payload = dict(self.valid_payload, card_number="4111111111111112")
        status, body = call(self.app, "POST", "/v1/transactions/validate", payload)
        self.assertEqual(status, "200 OK")
        self.assertEqual(body["status"], "rejected")
        self.assertIn("invalid_card_number", body["reasons"])

    def test_expired_card_rejected(self):
        payload = dict(self.valid_payload, expiry_month=1, expiry_year=2000)
        status, body = call(self.app, "POST", "/v1/transactions/validate", payload)
        self.assertEqual(body["status"], "rejected")
        self.assertIn("expired_card", body["reasons"])

    def test_unsupported_currency_rejected(self):
        payload = dict(self.valid_payload, currency="XXX")
        status, body = call(self.app, "POST", "/v1/transactions/validate", payload)
        self.assertEqual(body["status"], "rejected")
        self.assertIn("unsupported_currency", body["reasons"])

    def test_blacklist_rejected(self):
        token_hash = validation.hash_token(self.valid_payload["card_number"])
        self.storage.add_blacklist(token_hash, "stolen")
        status, body = call(self.app, "POST", "/v1/transactions/validate", self.valid_payload)
        self.assertEqual(body["status"], "rejected")
        self.assertIn("blacklisted", body["reasons"])

    def test_idempotency(self):
        payload = dict(self.valid_payload, idempotency_key="abc-123")
        status1, body1 = call(self.app, "POST", "/v1/transactions/validate", payload)
        status2, body2 = call(self.app, "POST", "/v1/transactions/validate", payload)
        self.assertEqual(body1, body2)

    def test_daily_limit_flagged(self):
        payload = dict(self.valid_payload, amount_cents=4000)
        call(self.app, "POST", "/v1/transactions/validate", payload)
        status, body = call(self.app, "POST", "/v1/transactions/validate", payload)
        self.assertEqual(body["status"], "flagged")
        self.assertIn("daily_limit_exceeded", body["reasons"])

    def test_bad_request(self):
        status, body = call(self.app, "POST", "/v1/transactions/validate", {"card_number": "x"})
        self.assertEqual(status, "400 Bad Request")


if __name__ == "__main__":
    unittest.main()
