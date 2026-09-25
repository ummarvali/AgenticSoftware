"""Business logic: validation, dedupe, short-code assignment, analytics and
background workers (async click recording, expiration sweeping).

Click events are pushed onto an in-process queue and drained by a background
thread so the redirect request path never waits on an analytics write.
"""
import hashlib
import logging
import queue
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from .shortcode import generate_short_code
from .validator import ValidationError, validate_custom_alias, validate_long_url

logger = logging.getLogger(__name__)


class NotFoundError(Exception):
    """Raised when a short code does not exist."""


class ExpiredError(Exception):
    """Raised when a short code exists but has expired."""


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class URLService:
    """Coordinates storage, cache and background workers for the shortener."""

    def __init__(self, cfg, storage, cache):
        self.config = cfg
        self.storage = storage
        self.cache = cache
        self._event_queue = queue.Queue()
        self._stop_event = threading.Event()
        self._metrics = {}
        self._metrics_lock = threading.Lock()
        threading.Thread(target=self._analytics_worker, daemon=True).start()
        threading.Thread(target=self._sweeper_worker, daemon=True).start()

    # -- metrics ---------------------------------------------------------
    def inc_metric(self, name, amount=1):
        with self._metrics_lock:
            self._metrics[name] = self._metrics.get(name, 0) + amount

    def get_metrics(self):
        with self._metrics_lock:
            return dict(self._metrics)

    # -- background workers ------------------------------------------------
    def _analytics_worker(self):
        while True:
            try:
                item = self._event_queue.get(timeout=0.5)
            except queue.Empty:
                if self._stop_event.is_set():
                    return
                continue
            try:
                short_code, clicked_at, referrer, user_agent, ip_hash = item
                self.storage.insert_event(short_code, clicked_at, referrer, user_agent, ip_hash)
                self.storage.increment_click(short_code)
            except Exception:
                logger.exception("failed to record analytics event")
            finally:
                self._event_queue.task_done()

    def _sweeper_worker(self):
        while not self._stop_event.is_set():
            try:
                codes = self.storage.purge_expired(_now_iso())
                for code in codes:
                    self.cache.evict(code)
            except Exception:
                logger.exception("expiration sweep failed")
            self._stop_event.wait(self.config.sweeper_interval_seconds)

    def shutdown(self):
        """Signal background threads to stop (daemon threads, best-effort)."""
        self._stop_event.set()

    def flush_analytics(self):
        """Block until all queued click events have been persisted (test/ops helper)."""
        self._event_queue.join()

    # -- core operations ---------------------------------------------------
    def create_short_url(self, long_url, custom_alias=None, ttl_seconds=None, api_key_id=None):
        long_url = validate_long_url(long_url, self.config)
        if ttl_seconds is not None and not isinstance(ttl_seconds, int):
            raise ValidationError("ttl_seconds must be an integer")
        created_at = _now_iso()
        expires_at = None
        if ttl_seconds is not None:
            expires_dt = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
            expires_at = expires_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        long_hash = hashlib.sha256(long_url.encode("utf-8")).hexdigest()

        if custom_alias:
            short_code = validate_custom_alias(custom_alias)
            try:
                self.storage.insert_url(
                    short_code, long_url, long_hash, created_at, expires_at, True, api_key_id
                )
            except sqlite3.IntegrityError:
                raise ValidationError("custom_alias already in use")
            reused = False
        else:
            if ttl_seconds is None:
                existing = self.storage.find_active_by_hash(long_hash)
                if existing:
                    self.cache.put(
                        existing["short_code"], (existing["long_url"], existing["expires_at"])
                    )
                    return {
                        "short_code": existing["short_code"],
                        "long_url": existing["long_url"],
                        "created_at": existing["created_at"],
                        "expires_at": existing["expires_at"],
                        "reused_existing": True,
                    }
            short_code = None
            for _ in range(5):
                candidate = generate_short_code(self.config.short_code_length)
                try:
                    self.storage.insert_url(
                        candidate, long_url, long_hash, created_at, expires_at, False, api_key_id
                    )
                    short_code = candidate
                    break
                except sqlite3.IntegrityError:
                    continue
            if short_code is None:
                raise RuntimeError("could not allocate a unique short code")
            reused = False

        self.cache.put(short_code, (long_url, expires_at))
        return {
            "short_code": short_code,
            "long_url": long_url,
            "created_at": created_at,
            "expires_at": expires_at,
            "reused_existing": reused,
        }

    def resolve(self, short_code, referrer=None, user_agent=None, client_addr=None):
        cached = self.cache.get(short_code)
        if cached is not None:
            long_url, expires_at = cached
        else:
            row = self.storage.get_by_short_code(short_code)
            if not row:
                raise NotFoundError()
            long_url, expires_at = row["long_url"], row["expires_at"]
            self.cache.put(short_code, (long_url, expires_at))

        if expires_at and expires_at <= _now_iso():
            self.cache.evict(short_code)
            raise ExpiredError()

        ip_hash = (
            hashlib.sha256(client_addr.encode("utf-8")).hexdigest()[:16] if client_addr else None
        )
        self._event_queue.put((short_code, _now_iso(), referrer, user_agent, ip_hash))
        return long_url

    def get_url_info(self, short_code):
        row = self.storage.get_by_short_code(short_code)
        if not row:
            raise NotFoundError()
        return {
            "short_code": row["short_code"],
            "long_url": row["long_url"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "click_count": row["click_count"],
        }

    def get_analytics(self, short_code):
        row = self.storage.get_by_short_code(short_code)
        if not row:
            raise NotFoundError()
        recent = self.storage.get_recent_events(short_code, limit=20)
        return {
            "short_code": short_code,
            "total_clicks": row["click_count"],
            "clicks_by_referrer": self.storage.get_referrer_counts(short_code),
            "recent_events": [
                {
                    "clicked_at": r["clicked_at"],
                    "referrer": r["referrer"],
                    "user_agent": r["user_agent"],
                }
                for r in recent
            ],
        }

    def create_api_key(self):
        key_value = secrets.token_urlsafe(24)
        created_at = _now_iso()
        self.storage.create_api_key(key_value, created_at, self.config.default_rate_limit_per_min)
        return {
            "api_key": key_value,
            "rate_limit_per_min": self.config.default_rate_limit_per_min,
            "created_at": created_at,
        }

    def verify_api_key(self, key_value):
        """Return the api_keys row if key_value is a known, active key, else None."""
        return self.storage.get_api_key(key_value)

    def health(self):
        db_ok = self.storage.health_check()
        return {
            "status": "ok" if db_ok else "degraded",
            "db": "ok" if db_ok else "error",
            "cache_size": len(self.cache),
        }
