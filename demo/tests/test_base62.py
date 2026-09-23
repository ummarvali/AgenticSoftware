"""Unit tests for the base62 codec."""

import unittest

from url_shortener import base62


class Base62Tests(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(base62.encode(0), "0")

    def test_round_trip(self):
        for n in [1, 61, 62, 63, 12345, 999999, 2 ** 32]:
            self.assertEqual(base62.decode(base62.encode(n)), n)

    def test_negative_rejected(self):
        with self.assertRaises(ValueError):
            base62.encode(-1)

    def test_invalid_char_rejected(self):
        with self.assertRaises(ValueError):
            base62.decode("!!")


if __name__ == "__main__":
    unittest.main()
