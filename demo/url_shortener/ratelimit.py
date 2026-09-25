"""In-memory fixed-window rate limiting with a swappable storage backend.

Clients are identified by API key (``Authorization`` / ``X-API-Key`` header)
when present, otherwise by remote IP address. Counts are bucketed into fixed
windows of ``window_seconds``; a counter resets automatically once the window
rolls over. The storage backend sits behind :class:`RateLimitStore` so it can
later be swapped for a shared backend (e.g. Redis) to keep counters consistent
across horizontally-scaled instances, without touching the limiter logic.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


class RateLimitStore(Protocol):
    """Storage contract for fixed-window counters, swappable for Redis etc."""

    def increment(self, key: str, window: int) -> int: ...
    def reset(self, key: Optional[str] = None) -> None: ...


class InMemoryRateLimitStore:
    """Dict-backed fixed-window counter store guarded by a lock.

    Degrades gracefully: any failure while reading/writing counters is treated
    as "allow" rather than propagated, so a storage hiccup never takes down the
    whole API (no single point of failure for the happy path).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key -> (window_index, count)
        self._counters: dict[str, tuple[int, int]] = {}

    def increment(self, key: str, window: int) -> int:
        try:
            with self._lock:
                current_window, count = self._counters.get(key, (window, 0))
                if current_window != window:
                    count = 0
                    current_window = window
                count += 1
                self._counters[key] = (current_window, count)
                return count
        except Exception:  # pragma: no cover - defensive graceful degradation
            logger.warning("rate limit store failure; allowing request", exc_info=True)
            return 1

    def reset(self, key: Optional[str] = None) -> None:
        try:
            with self._lock:
                if key is None:
                    self._counters.clear()
                else:
                    self._counters.pop(key, None)
        except Exception:  # pragma: no cover - defensive
            logger.warning("rate limit store reset failure", exc_info=True)


class RateLimitMetrics:
    """Tiny in-process counters exposed via the ``/metrics`` endpoint."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total_requests = 0
        self.blocked_requests = 0
        self.per_client_blocks: dict[str, int] = {}

    def record_request(self) -> None:
        with self._lock:
            self.total_requests += 1

    def record_block(self, key: str) -> None:
        with self._lock:
            self.blocked_requests += 1
            self.per_client_blocks[key] = self.per_client_blocks.get(key, 0) + 1
        logger.info("rate limit exceeded for client=%s", key)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "total_requests": self.total_requests,
                "blocked_requests": self.blocked_requests,
                "per_client_blocks": dict(self.per_client_blocks),
            }


class RateLimiter:
    """Fixed-window rate limiter with a configurable, hot-reloadable policy."""

    def __init__(
        self,
        limit: int = 60,
        window_seconds: float = 60.0,
        allowlist: Optional[list] = None,
        store: Optional[RateLimitStore] = None,
        metrics: Optional[RateLimitMetrics] = None,
        clock=time.time,
    ) -> None:
        self._lock = threading.Lock()
        self.limit = limit
        self.window_seconds = window_seconds
        self.allowlist = set(allowlist or [])
        self.store = store or InMemoryRateLimitStore()
        self.metrics = metrics or RateLimitMetrics()
        self._clock = clock

    def get_config(self) -> dict:
        with self._lock:
            return {
                "limit": self.limit,
                "window_seconds": self.window_seconds,
                "allowlist": sorted(self.allowlist),
            }

    def update_config(
        self,
        limit: Optional[int] = None,
        window_seconds: Optional[float] = None,
        allowlist: Optional[list] = None,
    ) -> dict:
        """Reconfigure thresholds/allowlist at runtime; unset fields are kept."""

        with self._lock:
            if limit is not None:
                self.limit = int(limit)
            if window_seconds is not None:
                self.window_seconds = float(window_seconds)
            if allowlist is not None:
                self.allowlist = set(allowlist)
        return self.get_config()

    @staticmethod
    def identify(environ) -> str:
        """Client key: API key from headers if present, else remote IP."""

        api_key = environ.get("HTTP_X_API_KEY")
        if api_key:
            return f"key:{api_key}"
        auth = environ.get("HTTP_AUTHORIZATION")
        if auth:
            return f"key:{auth}"
        return f"ip:{environ.get('REMOTE_ADDR', '?')}"

    def check(self, key: str) -> dict:
        """Return a rate-limit decision plus X-RateLimit-* metadata; never raises."""

        with self._lock:
            limit = self.limit
            window_seconds = self.window_seconds
            allowlisted = key in self.allowlist

        self.metrics.record_request()

        now = self._clock()
        window_index = int(now // window_seconds) if window_seconds > 0 else 0
        reset_at = (window_index + 1) * window_seconds if window_seconds > 0 else now

        if allowlisted:
            return {"allowed": True, "limit": limit, "remaining": limit, "reset": int(reset_at)}

        try:
            count = self.store.increment(key, window_index)
        except Exception:  # pragma: no cover - defensive graceful degradation
            logger.warning("rate limiter check failed; allowing request", exc_info=True)
            return {"allowed": True, "limit": limit, "remaining": limit, "reset": int(reset_at)}

        remaining = max(limit - count, 0)
        allowed = count <= limit
        if not allowed:
            self.metrics.record_block(key)
        return {"allowed": allowed, "limit": limit, "remaining": remaining, "reset": int(reset_at)}

    def allow(self, key: str) -> bool:
        """Backward-compatible boolean check used by simple integrations."""

        return self.check(key)["allowed"]

    def reset(self, key: Optional[str] = None) -> None:
        """Clear counters for one client (or everyone if ``key`` is None)."""

        self.store.reset(key)
