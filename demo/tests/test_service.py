"""Unit tests for the shortener service and both storage backends."""

import time
import unittest

from url_shortener.service import AliasError, InvalidURLError, ShortenerService
from url_shortener.store import InMemoryStore, SqliteStore


class CountingStore(InMemoryStore):
    """InMemoryStore that counts calls to `get`, used to assert cache hits."""

    def __init__(self):
        super().__init__()
        self.get_calls = 0

    def get(self, code):
        self.get_calls += 1
        return super().get(code)


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


class ServiceCacheTests(unittest.TestCase):
    """Verify the redirect-lookup cache is fast, correct, and never stale."""

    def test_cache_hit_avoids_repeated_store_lookups(self):
        store = CountingStore()
        svc = ShortenerService(store=store)
        rec = svc.shorten("https://example.com/cache")
        self.assertEqual(store.get_calls, 0)  # shorten() doesn't call get()

        self.assertEqual(svc.resolve(rec.code), "https://example.com/cache")
        self.assertEqual(store.get_calls, 1)  # first resolve: cache miss -> store

        self.assertEqual(svc.resolve(rec.code), "https://example.com/cache")
        self.assertEqual(svc.resolve(rec.code), "https://example.com/cache")
        self.assertEqual(store.get_calls, 1)  # subsequent resolves: served from cache

    def test_delete_invalidates_cache_no_stale_redirect(self):
        store = CountingStore()
        svc = ShortenerService(store=store)
        rec = svc.shorten("https://example.com/del")
        self.assertEqual(svc.resolve(rec.code), "https://example.com/del")  # populate cache

        self.assertTrue(svc.delete_link(rec.code))
        self.assertIsNone(svc.resolve(rec.code))  # must not serve stale cached value

    def test_delete_unknown_code_returns_false(self):
        svc = ShortenerService(store=InMemoryStore())
        self.assertFalse(svc.delete_link("nope"))

    def test_expired_cache_entry_treated_as_miss(self):
        store = CountingStore()
        svc = ShortenerService(store=store)
        rec = svc.shorten("https://example.com/exp", ttl_seconds=0.05)

        # Still valid: gets cached.
        self.assertEqual(svc.resolve(rec.code), "https://example.com/exp")

        time.sleep(0.1)

        # Now expired: must be treated as a miss, not served from the cache.
        self.assertIsNone(svc.resolve(rec.code))

    def test_cache_is_bounded(self):
        from url_shortener.cache import LRUCache

        svc = ShortenerService(store=InMemoryStore(), cache=LRUCache(max_size=2))
        a = svc.shorten("https://example.com/1")
        b = svc.shorten("https://example.com/2")
        c = svc.shorten("https://example.com/3")

        svc.resolve(a.code)
        svc.resolve(b.code)
        svc.resolve(c.code)  # evicts 'a' from the cache (max_size=2)

        # All three still resolve correctly via store fallback; correctness
        # is unaffected by eviction.
        self.assertEqual(svc.resolve(a.code), "https://example.com/1")
        self.assertEqual(svc.resolve(b.code), "https://example.com/2")
        self.assertEqual(svc.resolve(c.code), "https://example.com/3")


if __name__ == "__main__":
    unittest.main()
