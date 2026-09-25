"""Unit tests for the validation engine."""
import unittest

from cardvalidator.rules import RulesConfig
from cardvalidator.store import Store
from cardvalidator.engine import ValidationEngine


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.rules = RulesConfig(None)
        self.store = Store(":memory:")
        self.engine = ValidationEngine(self.rules, self.store)

    def valid_txn(self, **overrides):
        txn = {
            "transaction_id": "tx1",
            "card_number": "4111111111111111",
            "expiry_month": 12,
            "expiry_year": 2099,
            "cvv": "123",
            "amount": 100.0,
            "currency": "USD",
            "merchant_id": "m1",
        }
        txn.update(overrides)
        return txn

    def test_approved(self):
        result = self.engine.validate(self.valid_txn())
        self.assertEqual(result["decision"], "APPROVED")
        self.assertEqual(result["reason_codes"], [])
        self.assertEqual(result["masked_card"]["network"], "VISA")
        self.assertEqual(result["masked_card"]["last4"], "1111")

    def test_invalid_luhn(self):
        result = self.engine.validate(self.valid_txn(card_number="4111111111111112"))
        self.assertEqual(result["decision"], "DECLINED")
        self.assertIn("INVALID_CARD_FORMAT", result["reason_codes"])

    def test_expired_card(self):
        result = self.engine.validate(self.valid_txn(expiry_year=2000, expiry_month=1))
        self.assertEqual(result["decision"], "DECLINED")
        self.assertIn("EXPIRED_CARD", result["reason_codes"])

    def test_invalid_cvv(self):
        result = self.engine.validate(self.valid_txn(cvv="12"))
        self.assertIn("INVALID_CVV", result["reason_codes"])

    def test_amount_too_high(self):
        result = self.engine.validate(self.valid_txn(amount=999999.0))
        self.assertIn("AMOUNT_TOO_HIGH", result["reason_codes"])

    def test_blacklist(self):
        token = self.engine.validate(self.valid_txn())["card_token"]
        self.store.add_blacklist(token, "fraud")
        result = self.engine.validate(self.valid_txn(transaction_id="tx2"))
        self.assertIn("BLACKLISTED", result["reason_codes"])

    def test_velocity_exceeded(self):
        rules = self.rules.get()
        max_count = rules["velocity_max_count"]
        for i in range(max_count):
            self.engine.validate(self.valid_txn(transaction_id="tx{}".format(i)))
        result = self.engine.validate(self.valid_txn(transaction_id="tx_final"))
        self.assertIn("VELOCITY_EXCEEDED", result["reason_codes"])


if __name__ == "__main__":
    unittest.main()
