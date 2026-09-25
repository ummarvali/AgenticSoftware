"""Input validation, blacklist checks and a token-bucket rate limiter.

Kept dependency-free (standard library only) and simple enough for a
single-process prototype; the interface (`allow(key)`) mirrors what a
distributed rate limiter (e.g. Redis-backed) would offer.
"""

import re
import time
import threading
from urllib.parse import urlparse

ALLOWED_SCHEMES = {"http", "https"}
MAX_URL_LENGTH = 2048
ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
RESERVED_ALIASES = {"urls", "users", "api", "v1"}


class ValidationError(Exception):
    status = 400


def validate_long_url(url):
    if not url or not isinstance(url, str):
        raise ValidationError("long_url is required")
    if len(url) > MAX_URL_LENGTH:
        raise ValidationError("long_url exceeds maximum length")
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValidationError("long_url must use http or https scheme")
    if not parsed.netloc:
        raise ValidationError("long_url must include a host")
    return True


def is_blacklisted(url, patterns):
    lowered = url.lower()
    return any(p.lower() in lowered for p in patterns)


def validate_alias(alias):
    if not ALIAS_RE.match(alias):
        raise ValidationError(
            "custom_alias must be 3-32 chars of letters, digits, - or _"
        )
    if alias.lower() in RESERVED_ALIASES:
        raise ValidationError("custom_alias is reserved")
    return True


class RateLimiter:
    """Simple per-key token bucket rate limiter."""

    def __init__(self, capacity=30, refill_rate=5.0):
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate)
        self._buckets = {}
        self._lock = threading.Lock()

    def allow(self, key):
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill_rate)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                return False
            tokens -= 1.0
            self._buckets[key] = (tokens, now)
            return True

