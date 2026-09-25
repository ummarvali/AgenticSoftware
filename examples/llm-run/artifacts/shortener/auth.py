"""API key management and a simple per-client token-bucket rate limiter."""
import secrets
import threading
import time
from datetime import datetime, timezone


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def create_api_key(db, owner_name):
    """Generate and persist a new API key for a given owner name."""
    key = secrets.token_urlsafe(24)
    created = now_iso()
    db.execute(
        "INSERT INTO api_keys (key, owner_name, created_at, active) VALUES (?, ?, ?, 1)",
        (key, owner_name, created),
    )
    return {"api_key": key, "owner_name": owner_name, "created_at": created}


def verify_api_key(db, key):
    """Return the owner name for an active API key, or None if unknown/inactive."""
    if not key:
        return None
    row = db.query_one("SELECT * FROM api_keys WHERE key = ? AND active = 1", (key,))
    return row["owner_name"] if row else None


class RateLimiter:
    """Token-bucket rate limiter keyed by an arbitrary client identifier (API key or IP)."""

    def __init__(self, capacity=50, refill_rate=20.0):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._buckets = {}
        self._lock = threading.Lock()

    def allow(self, identifier):
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(identifier, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill_rate)
            if tokens < 1:
                self._buckets[identifier] = (tokens, now)
                return False
            tokens -= 1
            self._buckets[identifier] = (tokens, now)
            return True
