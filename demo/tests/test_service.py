"""Unit tests for the shortener service and both storage backends."""

import unittest

from url_shortener.service import AliasError, InvalidURLError, ShortenerService
from url_shortener.store import InMemoryStore, SqliteStore


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.svc = ShortenerService(store=InMemoryStore(), base_url="http://sho.rt")

    def test_shorten_and_resolve(self):
        rec = self.svc.shorten("https://example.com/page")
        self.assertTrue(rec.code)
        self.assertEqual(self.svc.resolve(rec.code), "https://example.com/page")

    def test_short_url_format(self):
        rec = self.svc.shorten("https://example.com/x")
        self.assertEqual(self.svc.short_url(rec.code), f"http://sho.rt/{rec.code}")

    def test_idempotent_for_same_url(self):
        a = self.svc.shorten("https://example.com")
        b = self.svc.shorten("https://example.com")
        self.assertEqual(a.code, b.code)

    def test_custom_alias(self):
        rec = self.svc.shorten("https://example.com", custom_alias="promo")
        self.assertEqual(rec.code, "promo")
        self.assertEqual(self.svc.resolve("promo"), "https://example.com")

    def test_duplicate_alias_rejected(self):
        self.svc.shorten("https://a.com", custom_alias="dup")
        with self.assertRaises(AliasError):
            self.svc.shorten("https://b.com", custom_alias="dup")

    def test_bad_alias_rejected(self):
        with self.assertRaises(AliasError):
            self.svc.shorten("https://a.com", custom_alias="no spaces!")

    def test_invalid_url_rejected(self):
        for bad in ["", "ftp://x", "notaurl", "javascript:alert(1)"]:
            with self.assertRaises(InvalidURLError):
                self.svc.shorten(bad)

    def test_expiry(self):
        rec = self.svc.shorten("https://example.com/expired", ttl_seconds=-1)
        self.assertIsNone(self.svc.resolve(rec.code))

    def test_unknown_code(self):
        self.assertIsNone(self.svc.resolve("missing"))

    def test_stats_counts_clicks(self):
        rec = self.svc.shorten("https://example.com/y")
        self.svc.resolve(rec.code)
        self.svc.resolve(rec.code)
        self.assertEqual(self.svc.stats(rec.code)["clicks"], 2)

    def test_sqlite_backend(self):
        svc = ShortenerService(store=SqliteStore(":memory:"))
        rec = svc.shorten("https://example.com/db")
        self.assertEqual(svc.resolve(rec.code), "https://example.com/db")
        self.assertEqual(svc.stats(rec.code)["clicks"], 1)


if __name__ == "__main__":
    unittest.main()
