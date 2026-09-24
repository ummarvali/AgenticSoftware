"""Unit tests for the pure validation functions."""
import unittest
import datetime

from cardvalidator import validation


class ValidationTests(unittest.TestCase):
    def test_luhn_valid(self):
        self.assertTrue(validation.luhn_check("4111111111111111"))

    def test_luhn_invalid(self):
        self.assertFalse(validation.luhn_check("4111111111111112"))

    def test_detect_network(self):
        self.assertEqual(validation.detect_network("4111111111111111"), "visa")
        self.assertEqual(validation.detect_network("5500000000000004"), "mastercard")
        self.assertEqual(validation.detect_network("378282246310005"), "amex")
        self.assertEqual(validation.detect_network("6011000000000004"), "discover")
        self.assertEqual(validation.detect_network("9999999999999999"), "unknown")

    def test_cvv_length(self):
        self.assertTrue(validation.validate_cvv("123", "visa"))
        self.assertFalse(validation.validate_cvv("12", "visa"))
        self.assertTrue(validation.validate_cvv("1234", "amex"))
        self.assertFalse(validation.validate_cvv("123", "amex"))

    def test_expiry(self):
        now = datetime.datetime(2024, 6, 15)
        self.assertTrue(validation.validate_expiry(6, 2024, now=now))
        self.assertFalse(validation.validate_expiry(5, 2024, now=now))

    def test_amount_bounds(self):
        self.assertTrue(validation.validate_amount(100, 1, 1000))
        self.assertFalse(validation.validate_amount(0, 1, 1000))
        self.assertFalse(validation.validate_amount(1001, 1, 1000))

    def test_currency(self):
        self.assertTrue(validation.validate_currency("USD", {"USD", "EUR"}))
        self.assertFalse(validation.validate_currency("XXX", {"USD", "EUR"}))

    def test_mask_pan(self):
        self.assertEqual(validation.mask_pan("4111111111111111"), "411111*******1111")

    def test_sanitize_payload_valid(self):
        payload = {
            "card_number": "4111111111111111", "cvv": "123",
            "expiry_month": 12, "expiry_year": 2099,
            "amount_cents": 500, "currency": "USD",
        }
        tx = validation.sanitize_payload(payload)
        self.assertEqual(tx["card_number"], "4111111111111111")

    def test_sanitize_payload_invalid(self):
        with self.assertRaises(ValueError):
            validation.sanitize_payload({"card_number": "abc"})


if __name__ == "__main__":
    unittest.main()
