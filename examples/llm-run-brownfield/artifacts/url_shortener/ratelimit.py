"""Rate limiting layer for the URL shortener API.

Design goals (see docs/ARCHITECTURE.md for the wider picture):

* A small storage interface (:class:`RateLimiterStore`) decouples the counting
  algorithm from where counts live, so the default in-memory dict-backed store
  can later be swapped for a shared store (e.g. Redis) without touching call
  sites in ``api.py``.
* :class:`RateLimiter` classifies a client (by IP, optionally honouring
  ``X-Forwarded-For`` when the peer is a configured trusted proxy), looks up a
  named rule (limit + window), and applies a thread-safe sliding-window
  counter keyed by ``(rule_name, client_id)``.
* Rules can be hot-reloaded at runtime via :meth:`RateLimiter.set_rule` (used
  by the ``PUT /admin/config/rate-limits/{ruleName}`` endpoint) without a
  process restart.
* Every allow/deny decision is counted in :class:`RateLimitMetrics` and logged,
  so abuse can be monitored via ``GET /admin/metrics``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitRule:
    """A named limit: at most ``limit`` requests per ``window_seconds``."""

    limit: int
    window_seconds: float


@dataclass
class RateLimitResult:
    """Outcome of a single rate-limit check, ready to become HTTP headers."""

    allowed: bool
    limit: Optional[int]
    remaining: Optional[int]
    reset_at: Optional[float]

    def headers(self) -> list:
        """Standard ``X-RateLimit-*`` headers; empty if no rule applied."""

        if self.limit is None:
            return []
        now = time.time()
        reset_in = max(0, int(round((self.reset_at or now) - now)))
        return [
            ("X-RateLimit-Limit", str(self.limit)),
            ("X-RateLimit-Remaining", str(max(0, self.remaining or 0))),
            ("X-RateLimit-Reset", str(reset_in)),
        ]


class RateLimiterStore(Protocol):
    """Storage contract for sliding-window counters.

    Implementations must be safe to call concurrently from multiple threads
    (the standard-library WSGI server serves each request on its own thread
    when used with a threading mixin, and tests exercise this directly).
    """

    def hit(
        self, key: str, limit: int, window_seconds: float, now: Optional[float] = None
    ):
        """Record a hit for ``key`` and report whether it is within budget.

        Returns ``(allowed, remaining, reset_at)``.
        """
        ...


class InMemoryRateLimiterStore:
    """Sliding-window counter backed by a dict of deques, guarded by a lock.

    Kept intentionally simple (a timestamp log per key) since the service is
    single-instance; the :class:`RateLimiterStore` protocol is what makes it
    possible to later swap this for a shared backend (Redis, etc.) without any
    change to :class:`RateLimiter` or the API layer.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict = {}

    def hit(self, key: str, limit: int, window_seconds: float, now: Optional[float] = None):
        now = time.time() if now is None else now
        cutoff = now - window_seconds
        with self._lock:
            dq = self._buckets.setdefault(key, deque())
            while dq and dq[0] <= cutoff:
                dq.popleft()
            allowed = len(dq) < limit
            if allowed:
                dq.append(now)
            remaining = max(0, limit - len(dq))
            reset_at = (dq[0] + window_seconds) if dq else (now + window_seconds)
            return allowed, remaining, reset_at


class RateLimitMetrics:
    """In-memory counters of allow/deny decisions per rule, for monitoring."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict = {}

    def record(self, rule_name: str, allowed: bool) -> None:
        with self._lock:
            key = (rule_name, allowed)
            self._counts[key] = self._counts.get(key, 0) + 1

    def snapshot(self) -> dict:
        with self._lock:
            result: dict = {}
            for (rule_name, allowed), count in self._counts.items():
                bucket = result.setdefault(rule_name, {"allowed": 0, "denied": 0})
                bucket["allowed" if allowed else "denied"] += count
            return result


class RateLimiter:
    """Classifies clients, applies named rules, and records outcomes."""

    def __init__(
        self,
        rules: dict,
        store: Optional[RateLimiterStore] = None,
        trusted_proxies: Optional[set] = None,
    ) -> None:
        self.store: RateLimiterStore = store or InMemoryRateLimiterStore()
        self._rules_lock = threading.Lock()
        self._rules: dict = dict(rules)
        self.trusted_proxies = set(trusted_proxies or set())
        self.metrics = RateLimitMetrics()

    # -- rule management (hot-reloadable, no redeploy needed) ---------------
    def set_rule(self, name: str, limit: int, window_seconds: float) -> None:
        with self._rules_lock:
            self._rules[name] = RateLimitRule(int(limit), float(window_seconds))

    def get_rule(self, name: str) -> Optional[RateLimitRule]:
        with self._rules_lock:
            return self._rules.get(name)

    def rules_snapshot(self) -> dict:
        with self._rules_lock:
            return {
                name: {"limit": rule.limit, "window_seconds": rule.window_seconds}
                for name, rule in self._rules.items()
            }

    # -- client identification ----------------------------------------------
    def client_id(self, environ: dict) -> str:
        remote = environ.get("REMOTE_ADDR") or "unknown"
        trusted = self.trusted_proxies
        if trusted and ("*" in trusted or remote in trusted):
            forwarded = environ.get("HTTP_X_FORWARDED_FOR")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return remote

    # -- the actual check -----------------------------------------------------
    def check(self, rule_name: str, environ: dict, now: Optional[float] = None) -> RateLimitResult:
        rule = self.get_rule(rule_name)
        if rule is None:
            # No rule configured for this name: allow, no headers emitted.
            return RateLimitResult(True, None, None, None)
        client = self.client_id(environ)
        key = f"{rule_name}:{client}"
        allowed, remaining, reset_at = self.store.hit(key, rule.limit, rule.window_seconds, now=now)
        self.metrics.record(rule_name, allowed)
        if not allowed:
            logger.warning("rate limit exceeded: rule=%s client=%s", rule_name, client)
        return RateLimitResult(allowed, rule.limit, remaining, reset_at)


def build_default_limiter() -> RateLimiter:
    """Build a :class:`RateLimiter` from environment-driven config defaults."""

    from . import config

    rules = {
        "shorten": RateLimitRule(config.RATE_LIMIT_SHORTEN_LIMIT, config.RATE_LIMIT_SHORTEN_WINDOW),
        "redirect": RateLimitRule(config.RATE_LIMIT_REDIRECT_LIMIT, config.RATE_LIMIT_REDIRECT_WINDOW),
        "admin": RateLimitRule(config.RATE_LIMIT_ADMIN_LIMIT, config.RATE_LIMIT_ADMIN_WINDOW),
    }
    return RateLimiter(rules=rules, trusted_proxies=config.TRUSTED_PROXIES)
