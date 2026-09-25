"""Rate limiting middleware components.

Provides an in-memory sliding-window counter behind a small
``RateLimiterBackend`` protocol, a ``RateLimiter`` facade used by the API
layer to identify clients, check/update limits, keep metrics, and hot-reload
externalized configuration.

Fail-open policy (explicit, by design): if the limiter backend raises an
unexpected error while checking/updating a counter, the request is allowed
through and the error is counted/logged. The rate limiter must never become
the API's single point of failure. Set ``fail_mode="closed"`` (per endpoint
rule set, globally) to invert this and reject requests instead when the
backend is unhealthy.

Horizontal scaling seam: ``RateLimiterBackend`` is the only thing that talks
to storage. Swapping ``InMemorySlidingWindowBackend`` for a Redis-backed (or
similar shared-store) implementation that honours the same interface makes
rate limiting correct across multiple instances without touching call sites.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional, Protocol

logger = logging.getLogger("url_shortener.ratelimit")


@dataclass
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: float


class RateLimiterBackend(Protocol):
    """Storage contract for rate limit counters."""

    def check(self, key: str, limit: int, window_seconds: float) -> RateLimitResult: ...


class InMemorySlidingWindowBackend:
    """Sliding-window counter per key, guarded by a single lock.

    Memory is bounded per key to at most ``limit`` timestamps; :meth:`sweep`
    can be called periodically to drop idle keys and avoid unbounded growth
    across many distinct clients.
    """

    def __init__(self) -> None:
        self._buckets: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: float) -> RateLimitResult:
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            cutoff = now - window_seconds
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(0.0, bucket[0] + window_seconds - now)
                return RateLimitResult(False, limit, 0, retry_after)
            bucket.append(now)
            remaining = max(0, limit - len(bucket))
            return RateLimitResult(True, limit, remaining, 0.0)

    def sweep(self, max_idle_seconds: float = 3600) -> int:
        """Drop buckets with no recent activity; returns the number removed."""
        now = time.monotonic()
        removed = 0
        with self._lock:
            for key in list(self._buckets):
                bucket = self._buckets[key]
                if not bucket or bucket[-1] <= now - max_idle_seconds:
                    del self._buckets[key]
                    removed += 1
        return removed


@dataclass
class EndpointRule:
    limit: int
    window_seconds: float


@dataclass
class RateLimiterConfig:
    rules: Dict[str, EndpointRule] = field(default_factory=dict)
    fail_mode: str = "open"  # "open" | "closed"
    trusted_keys: frozenset = frozenset()
    trusted_multiplier: float = 5.0


class RateLimiter:
    """Ties client identification, backend counters, hot-reloadable config,
    and in-memory metrics together. This is what the API layer calls."""

    def __init__(
        self,
        config: RateLimiterConfig,
        backend: Optional[RateLimiterBackend] = None,
        config_path: str = "",
        poll_seconds: float = 5.0,
    ) -> None:
        self._config = config
        self.backend = backend or InMemorySlidingWindowBackend()
        self._config_path = config_path
        self._poll_seconds = poll_seconds
        self._config_mtime: Optional[float] = None
        self._config_lock = threading.Lock()
        self._metrics_lock = threading.Lock()
        self._metrics = {"allowed": 0, "blocked": 0, "errors": 0, "by_endpoint": {}}
        self._last_poll = 0.0
        if config_path:
            self._reload_config_if_changed(force=True)

    # -- configuration -------------------------------------------------
    def _reload_config_if_changed(self, force: bool = False) -> None:
        if not self._config_path:
            return
        now = time.monotonic()
        if not force and now - self._last_poll < self._poll_seconds:
            return
        self._last_poll = now
        try:
            mtime = os.path.getmtime(self._config_path)
        except OSError:
            return
        if not force and self._config_mtime == mtime:
            return
        try:
            with open(self._config_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            rules = {
                name: EndpointRule(int(v["limit"]), float(v["window_seconds"]))
                for name, v in data.get("rules", {}).items()
            }
            with self._config_lock:
                base = self._config
                fail_mode = data.get("fail_mode", base.fail_mode)
                trusted_keys = frozenset(data.get("trusted_keys", list(base.trusted_keys)))
                trusted_multiplier = float(data.get("trusted_multiplier", base.trusted_multiplier))
                self._config = RateLimiterConfig(
                    rules=rules or base.rules,
                    fail_mode=fail_mode,
                    trusted_keys=trusted_keys,
                    trusted_multiplier=trusted_multiplier,
                )
            self._config_mtime = mtime
            logger.info("rate limit config reloaded from %s", self._config_path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("failed to reload rate limit config %s: %s", self._config_path, exc)

    def _current_config(self) -> RateLimiterConfig:
        self._reload_config_if_changed()
        with self._config_lock:
            return self._config

    # -- client identification ------------------------------------------
    @staticmethod
    def identify(environ: dict, valid_api_keys: frozenset) -> "tuple[str, bool]":
        """Return ``(identity_key, is_trusted)``.

        A client-supplied API key header is only used as identity when it
        matches a server-configured set of known keys; otherwise identity
        always falls back to the connection's peer address (``REMOTE_ADDR``),
        never to an unverified header or query parameter value.
        """
        api_key = environ.get("HTTP_X_API_KEY")
        if api_key and api_key in valid_api_keys:
            return f"key:{api_key}", True
        return f"ip:{environ.get('REMOTE_ADDR', 'unknown')}", False

    # -- the check --------------------------------------------------------
    def check(self, endpoint: str, environ: dict) -> RateLimitResult:
        config = self._current_config()
        rule = config.rules.get(endpoint)
        if rule is None:
            # No rule configured for this endpoint: allow, unmetered.
            return RateLimitResult(True, 0, 0, 0.0)

        identity, trusted = self.identify(environ, config.trusted_keys)
        limit = rule.limit
        if trusted:
            limit = max(1, int(rule.limit * config.trusted_multiplier))

        key = f"{endpoint}:{identity}"
        try:
            result = self.backend.check(key, limit, rule.window_seconds)
        except Exception:  # noqa: BLE001 - fail-open: limiter must not break the API
            logger.exception("rate limiter backend error; key=%s", key)
            with self._metrics_lock:
                self._metrics["errors"] += 1
            if config.fail_mode == "closed":
                result = RateLimitResult(False, limit, 0, rule.window_seconds)
            else:
                result = RateLimitResult(True, limit, limit, 0.0)

        with self._metrics_lock:
            bucket = self._metrics["by_endpoint"].setdefault(endpoint, {"allowed": 0, "blocked": 0})
            if result.allowed:
                self._metrics["allowed"] += 1
                bucket["allowed"] += 1
            else:
                self._metrics["blocked"] += 1
                bucket["blocked"] += 1
                logger.info("rate limit exceeded endpoint=%s identity=%s", endpoint, identity)
        return result

    def metrics_snapshot(self) -> dict:
        with self._metrics_lock:
            return {
                "allowed": self._metrics["allowed"],
                "blocked": self._metrics["blocked"],
                "errors": self._metrics["errors"],
                "by_endpoint": {k: dict(v) for k, v in self._metrics["by_endpoint"].items()},
            }


def default_rate_limiter_config() -> RateLimiterConfig:
    from . import config as cfg

    return RateLimiterConfig(
        rules={
            "create": EndpointRule(cfg.RATE_LIMIT_CREATE_LIMIT, cfg.RATE_LIMIT_CREATE_WINDOW),
            "redirect": EndpointRule(cfg.RATE_LIMIT_REDIRECT_LIMIT, cfg.RATE_LIMIT_REDIRECT_WINDOW),
        },
        fail_mode=cfg.RATE_LIMIT_FAIL_MODE,
        trusted_keys=frozenset(cfg.TRUSTED_API_KEYS),
        trusted_multiplier=cfg.TRUSTED_RATE_LIMIT_MULTIPLIER,
    )


def build_rate_limiter() -> RateLimiter:
    """Build the production rate limiter from environment/config-file settings."""
    from . import config as cfg

    return RateLimiter(
        default_rate_limiter_config(),
        config_path=cfg.RATE_LIMIT_CONFIG_PATH,
        poll_seconds=cfg.RATE_LIMIT_CONFIG_POLL_SECONDS,
    )
