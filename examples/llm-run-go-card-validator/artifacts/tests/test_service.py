"""Unit tests for core validation logic plus in-process HTTP integration tests."""

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from cardvalidator import fraud as fraud_mod
from cardvalidator import masking
from cardvalidator import storage as storage_mod
from cardvalidator import validators
from cardvalidator.app import Application
from cardvalidator.config import Config
from cardvalidator.server import make_handler


class TestValidators(unittest.TestCase):
    def test_luhn_valid(self):
        self.assertTrue(validators.luhn_check("4111111111111111"))

    def test_luhn_invalid(self):
        self.assertFalse(validators.luhn_check("4111111111111112"))

    def test_detect_network_visa(self):
        self.assertEqual(validators.detect_network("4111111111111111"), "VISA")

    def test_detect_network_mastercard(self):
        self.assertEqual(validators.detect_network("5555555555554444"), "MASTERCARD")

    def test_detect_network_amex(self):
        self.assertEqual(validators.detect_network("378282246310005"), "AMEX")

    def test_detect_network_unknown(self):
        self.assertIsNone(validators.detect_network("999999999999"))

    def test_structural_expired(self):
        reasons, _ = validators.validate_structural("4111111111111111", 1, 2000, "123", None)
        self.assertIn("CARD_EXPIRED", reasons)

    def test_structural_valid(self):
        reasons, network = validators.validate_structural("4111111111111111", 12, 2099, "123", None)
        self.assertEqual(reasons, [])
        self.assertEqual(network, "VISA")

    def test_business_amount_out_of_range(self):
        config = Config(min_amount=1.0, max_amount=10.0)
        reasons = validators.validate_business(1000.0, "USD", "MERCH123", "VISA", config)
        self.assertIn("AMOUNT_OUT_OF_RANGE", reasons)

    def test_business_unsupported_currency(self):
        config = Config()
        reasons = validators.validate_business(10.0, "XYZ", "MERCH123", "VISA", config)
        self.assertIn("CURRENCY_UNSUPPORTED", reasons)


class TestMasking(unittest.TestCase):
    def test_mask_pan(self):
        m = masking.mask_pan("4111111111111111", "salt")
        self.assertEqual(m["bin"], "411111")
        self.assertEqual(m["last4"], "1111")
        self.assertEqual(len(m["hash"]), 64)
        self.assertNotIn("4111111111111111", m["masked"])


class TestFraud(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.storage = storage_mod.Storage(self.db_path)
        self.config = Config(velocity_max_attempts=2, velocity_window_seconds=60)
        self.engine = fraud_mod.FraudEngine(self.config, self.storage)

    def tearDown(self):
        self.storage.close()
        os.unlink(self.db_path)

    def test_blacklist_flag(self):
        self.storage.add_blacklist("a" * 64, "test", "now")
        reasons, risk = self.engine.check("a" * 64)
        self.assertIn("BLACKLISTED_PAN", reasons)
        self.assertGreater(risk, 0)

    def test_velocity_flag(self):
        h = "b" * 64
        self.engine.check(h)
        self.engine.check(h)
        reasons, _ = self.engine.check(h)
        self.assertIn("VELOCITY_EXCEEDED", reasons)


class IntegrationTestBase(unittest.TestCase):
    """Spins up the real HTTP server on an ephemeral port for the subclass tests."""

    @classmethod
    def setUpClass(cls):
        fd, cls.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        cls.config = Config(db_path=cls.db_path, api_keys=["test-key"])
        cls.app = Application(config=cls.config)
        handler = make_handler(cls.app)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.app.close()
        os.unlink(cls.db_path)

    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def _post(self, path, payload, headers=None):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self._url(path), data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            resp = urllib.request.urlopen(req)
            return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def _get(self, path, headers=None):
        req = urllib.request.Request(self._url(path), method="GET")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            resp = urllib.request.urlopen(req)
            ct = resp.headers.get("Content-Type", "")
            body = resp.read().decode()
            return resp.status, (json.loads(body) if "json" in ct else body)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            try:
                return e.code, json.loads(body)
            except json.JSONDecodeError:
                return e.code, body


class TestHealthEndpoints(IntegrationTestBase):
    def test_healthz(self):
        status, body = self._get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_readyz(self):
        status, body = self._get("/readyz")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["checks"]["db"], "ok")

    def test_metrics(self):
        status, body = self._get("/metrics")
        self.assertEqual(status, 200)
        self.assertIsInstance(body, str)

    def test_unknown_route_returns_404(self):
        status, body = self._get("/nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not_found")


class TestValidateEndpoint(IntegrationTestBase):
    VALID_PAYLOAD = {
        "pan": "4111111111111111",
        "expiry_month": 12,
        "expiry_year": 2099,
        "cvv": "123",
        "amount": 100.0,
        "currency": "USD",
        "merchant_id": "MERCH123",
    }

    def test_missing_api_key_returns_401(self):
        status, _ = self._post("/v1/validate", self.VALID_PAYLOAD)
        self.assertEqual(status, 401)

    def test_valid_transaction(self):
        status, body = self._post("/v1/validate", self.VALID_PAYLOAD, {"X-API-Key": "test-key"})
        self.assertEqual(status, 200)
        self.assertTrue(body["valid"])
        self.assertEqual(body["network"], "VISA")
        self.assertNotIn("4111111111111111", body["masked_pan"])

    def test_invalid_luhn_returns_invalid_result(self):
        payload = dict(self.VALID_PAYLOAD, pan="4111111111111112")
        status, body = self._post("/v1/validate", payload, {"X-API-Key": "test-key"})
        self.assertEqual(status, 200)
        self.assertFalse(body["valid"])
        self.assertIn("LUHN_FAILED", body["reason_codes"])

    def test_missing_field_returns_400(self):
        payload = dict(self.VALID_PAYLOAD)
        del payload["cvv"]
        status, body = self._post("/v1/validate", payload, {"X-API-Key": "test-key"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "missing_fields")

    def test_unexpected_field_returns_400(self):
        payload = dict(self.VALID_PAYLOAD, extra_field="x")
        status, body = self._post("/v1/validate", payload, {"X-API-Key": "test-key"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "unexpected_fields")

    def test_invalid_json_returns_400(self):
        req = urllib.request.Request(self._url("/v1/validate"), data=b"{not json", method="POST")
        req.add_header("X-API-Key", "test-key")
        req.add_header("Content-Type", "application/json")
        try:
            urllib.request.urlopen(req)
            self.fail("expected HTTPError")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)


class TestBlacklistAndAudit(IntegrationTestBase):
    def test_blacklist_add(self):
        hashed = "c" * 64
        status, body = self._post(
            "/v1/blacklist", {"hashed_pan": hashed, "reason": "fraud"}, {"X-API-Key": "test-key"}
        )
        self.assertEqual(status, 201)
        self.assertEqual(body["hashed_pan"], hashed)

    def test_blacklist_invalid_hash_returns_400(self):
        status, body = self._post(
            "/v1/blacklist", {"hashed_pan": "short"}, {"X-API-Key": "test-key"}
        )
        self.assertEqual(status, 400)

    def test_audit_round_trip(self):
        payload = TestValidateEndpoint.VALID_PAYLOAD
        status, body = self._post("/v1/validate", payload, {"X-API-Key": "test-key"})
        self.assertEqual(status, 200)
        request_id = body["request_id"]

        status2, body2 = self._get(f"/v1/audit/{request_id}", {"X-API-Key": "test-key"})
        self.assertEqual(status2, 200)
        self.assertEqual(body2["request_id"], request_id)
        self.assertNotIn("4111111111111111", body2["masked_pan"])

    def test_audit_not_found_returns_404(self):
        status, body = self._get("/v1/audit/does-not-exist", {"X-API-Key": "test-key"})
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not_found")


if __name__ == "__main__":
    unittest.main()
