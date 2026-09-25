"""Simple in-memory token-bucket rate limiter keyed by caller identity.

Callers must supply an identity key that has already been verified (e.g. a
confirmed API key or the raw peer IP address); this module never decides
identity itself, it only enforces a rate against whatever key it is given.
"""
import threading
import time


class RateLimiter:
    """Token bucket per key; refills continuously at rate_per_min/60 tokens/sec."""

    def __init__(self):
        self._buckets = {}
        self._lock = threading.Lock()

    def allow(self, key, rate_per_min):
        """Consume one token for `key`; return True if allowed, False if limited."""
        capacity = max(1, rate_per_min)
        refill_rate = capacity / 60.0
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(capacity), now))
            tokens = min(capacity, tokens + (now - last) * refill_rate)
            if tokens >= 1.0:
                tokens -= 1.0
                self._buckets[key] = (tokens, now)
                return True
            self._buckets[key] = (tokens, now)
            return False
