"""Unit tests for the core domain/service logic."""

import unittest
import tempfile
import os
import datetime

from urlshortener.storage import Storage
from urlshortener.service import (
    UrlShortenerService,
    ValidationError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
)
from urlshortener.validation import RateLimiter


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.storage = Storage(self.db_path)
        self.service = UrlShortenerService(self.storage)

    def tearDown(self):
        os.remove(self.db_path)

    def test_create_and_redirect(self):
        result = self.service.create_short_url("https://example.com/page")
        self.assertIn("short_code", result)
        row = self.service.get_url_for_redirect(result["short_code"])
        self.assertEqual(row["long_url"], "https://example.com/page")

    def test_invalid_url_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.create_short_url("not-a-url")

    def test_blacklisted_url_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.create_short_url("https://malware.test/bad")

    def test_custom_alias(self):
        result = self.service.create_short_url(
            "https://example.com", custom_alias="my-alias"
        )
        self.assertEqual(result["short_code"], "my-alias")

    def test_custom_alias_conflict(self):
        self.service.create_short_url("https://example.com", custom_alias="dup")
        with self.assertRaises(ConflictError):
            self.service.create_short_url("https://example.org", custom_alias="dup")

    def test_invalid_alias_format(self):
        with self.assertRaises(ValidationError):
            self.service.create_short_url("https://example.com", custom_alias="a")

    def test_reserved_alias_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.create_short_url("https://example.com", custom_alias="urls")

    def test_expired_url_not_redirected(self):
        result = self.service.create_short_url(
            "https://example.com", custom_alias="expiring",
            expires_at=(datetime.datetime.utcnow() + datetime.timedelta(seconds=0)
                        + datetime.timedelta(milliseconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        # Force expiry in the past by directly manipulating storage.
        self.storage._conn.execute(
            "UPDATE urls SET expires_at = ? WHERE short_code = ?",
            ("2000-01-01T00:00:00Z", result["short_code"]),
        )
        self.storage._conn.commit()
        with self.assertRaises(NotFoundError):
            self.service.get_url_for_redirect(result["short_code"])

    def test_expires_at_in_past_rejected_on_create(self):
        with self.assertRaises(ValidationError):
            self.service.create_short_url(
                "https://example.com", expires_at="2000-01-01T00:00:00Z"
            )

    def test_deactivate_and_redirect_fails(self):
        result = self.service.create_short_url("https://example.com")
        self.service.deactivate(result["short_code"])
        with self.assertRaises(NotFoundError):
            self.service.get_url_for_redirect(result["short_code"])

    def test_deactivate_owned_url_requires_owner(self):
        result = self.service.create_short_url("https://example.com", owner_id="user-1")
        with self.assertRaises(ForbiddenError):
            self.service.deactivate(result["short_code"], requester_owner_id="user-2")
        # Owner can deactivate successfully.
        self.service.deactivate(result["short_code"], requester_owner_id="user-1")

    def test_metadata_not_found(self):
        with self.assertRaises(NotFoundError):
            self.service.get_metadata("does-not-exist")

    def test_click_recording_and_analytics(self):
        result = self.service.create_short_url("https://example.com")
        code = result["short_code"]
        self.service.record_click(code, "https://ref.example", "Mozilla/5.0", "US")
        self.service.record_click(code, "https://ref.example", "curl/7.0", "DE")
        meta = self.service.get_metadata(code)
        self.assertEqual(meta["click_count"], 2)
        analytics = self.service.get_analytics(code)
        self.assertEqual(analytics["total_clicks"], 2)
        self.assertEqual(len(analytics["top_referrers"]), 1)
        self.assertEqual(analytics["top_referrers"][0]["count"], 2)
        countries = {g["country"] for g in analytics["geo_distribution"]}
        self.assertEqual(countries, {"US", "DE"})

    def test_create_user(self):
        user = self.service.create_user()
        self.assertIn("user_id", user)
        self.assertIn("api_token", user)
        resolved = self.service.authenticate(user["api_token"])
        self.assertEqual(resolved, user["user_id"])

    def test_rate_limiter_blocks_after_capacity(self):
        limiter = RateLimiter(capacity=2, refill_rate=0.0001)
        self.assertTrue(limiter.allow("k"))
        self.assertTrue(limiter.allow("k"))
        self.assertFalse(limiter.allow("k"))

    def test_create_rate_limit_enforced(self):
        service = UrlShortenerService(
            self.storage, create_rate_limiter=RateLimiter(capacity=1, refill_rate=0.0001)
        )
        service.create_short_url("https://example.com/a")
        with self.assertRaises(RateLimitError):
            service.create_short_url("https://example.com/b")


if __name__ == "__main__":
    unittest.main()

