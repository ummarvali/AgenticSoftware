"""Fraud/risk heuristics: blacklist lookup and sliding-window velocity checks."""

import threading
import time


class FraudEngine:
    """In-memory velocity counters plus durable blacklist lookups via storage."""

    def __init__(self, config, storage):
        self.config = config
        self.storage = storage
        self._velocity = {}
        self._lock = threading.Lock()

    def check(self, hashed_pan: str):
        """Return (reason_codes, risk_score) for a given hashed PAN."""
        reasons = []
        risk = 0.0

        if self.storage.is_blacklisted(hashed_pan):
            reasons.append("BLACKLISTED_PAN")
            risk += 0.9

        count = self._record_attempt(hashed_pan)
        if count > self.config.velocity_max_attempts:
            reasons.append("VELOCITY_EXCEEDED")
            risk += 0.5

        risk += min(0.05 * max(0, count - 1), 0.3)
        return reasons, round(min(risk, 1.0), 3)

    def _record_attempt(self, hashed_pan: str) -> int:
        now = time.time()
        window = self.config.velocity_window_seconds
        with self._lock:
            attempts = self._velocity.setdefault(hashed_pan, [])
            attempts[:] = [t for t in attempts if now - t < window]
            attempts.append(now)
            return len(attempts)
