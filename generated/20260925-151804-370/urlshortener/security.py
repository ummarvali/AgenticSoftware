"""API-key authentication and in-memory token-bucket rate limiting.

Identity/quota keys are never taken from unverified client-supplied
values: management endpoints key on the API key only after it has been
verified against the api_keys table, and the public redirect endpoint
keys on the socket peer address (REMOTE_ADDR), never on headers.
"""
import threading
import time


class AuthError(Exception):
    pass


class RateLimitError(Exception):
    pass


class TokenBucket:
    def __init__(self, rate_per_minute, capacity=None):
        self.rate_per_sec = rate_per_minute / 60.0
        self.capacity = capacity if capacity is not None else max(1, rate_per_minute)
        self.tokens = float(self.capacity)
        self.last = time.monotonic()
        self._lock = threading.Lock()

    def allow(self):
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.last
            self.last = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_sec)
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False


class RateLimiter:
    def __init__(self, rate_per_minute):
        self.rate_per_minute = rate_per_minute
        self._buckets = {}
        self._lock = threading.Lock()

    def check(self, identifier):
        with self._lock:
            bucket = self._buckets.get(identifier)
            if bucket is None:
                bucket = TokenBucket(self.rate_per_minute)
                self._buckets[identifier] = bucket
        if not bucket.allow():
            raise RateLimitError("rate limit exceeded")


def authenticate(db, environ):
    """Verify the X-API-Key header against the api_keys table. Returns
    (api_key, owner_name) on success, raises AuthError otherwise."""
    key = environ.get("HTTP_X_API_KEY")
    if not key:
        raise AuthError("missing API key")
    owner = db.verify_api_key(key)
    if not owner:
        raise AuthError("invalid API key")
    return key, owner
