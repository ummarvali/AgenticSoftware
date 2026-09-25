"""Core business logic: validate, shorten, resolve, and report on links."""

from __future__ import annotations

import re
import time
from typing import Optional
from urllib.parse import urlparse

from . import base62
from .cache import DEFAULT_MAX_SIZE, LRUCache
from .store import InMemoryStore, LinkRecord, Store

# Offset so the smallest auto-generated code is already several characters long,
# which avoids trivially guessable one-character slugs.
CODE_OFFSET = 100_000
ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")


class InvalidURLError(ValueError):
    """Raised when a submitted URL is missing, malformed, or uses a bad scheme."""


class AliasError(ValueError):
    """Raised when a custom alias is invalid or already taken."""


class ShortenerService:
    """Framework-agnostic core. The API layer is a thin adapter over this class.

    The hot redirect path (``resolve``) is fronted by a bounded, thread-safe
    in-memory LRU cache keyed by short code. Reads check the cache first and
    fall back to the store on a miss, populating the cache. Expiration is
    checked lazily on every read (cache or store), and deletions synchronously
    invalidate the cache entry so a deleted/expired link is never served stale.
    """

    def __init__(
        self,
        store: Optional[Store] = None,
        base_url: str = "http://localhost:8000",
        cache: Optional[LRUCache] = None,
        cache_max_size: int = DEFAULT_MAX_SIZE,
    ) -> None:
        self._store: Store = store or InMemoryStore()
        self.base_url = base_url.rstrip("/")
        self._cache: LRUCache = cache if cache is not None else LRUCache(max_size=cache_max_size)

    @property
    def store(self) -> Store:
        return self._store

    def shorten(
        self,
        long_url: str,
        custom_alias: Optional[str] = None,
        ttl_seconds: Optional[float] = None,
    ) -> LinkRecord:
        long_url = (long_url or "").strip()
        self._validate_url(long_url)
        now = time.time()
        expires_at = now + ttl_seconds if ttl_seconds is not None else None

        if custom_alias:
            if not ALIAS_RE.match(custom_alias):
                raise AliasError("alias must be 3-32 chars from [A-Za-z0-9_-]")
            if self._store.get(custom_alias) is not None:
                raise AliasError(f"alias '{custom_alias}' is already taken")
            record = LinkRecord(custom_alias, long_url, now, expires_at)
            return self._store.create_link(record)

        # Idempotency: an identical, still-valid URL returns its existing code so
        # we never mint duplicate slugs for the same destination.
        existing = self._store.find_by_url(long_url)
        if existing is not None and not existing.is_expired(now):
            return existing

        new_id = self._store.next_id()
        code = base62.encode(new_id + CODE_OFFSET)
        record = LinkRecord(code, long_url, now, expires_at)
        return self._store.create_link(record)

    def resolve(
        self,
        code: str,
        *,
        referrer: Optional[str] = None,
        user_agent: Optional[str] = None,
        now: Optional[float] = None,
    ) -> Optional[str]:
        now = time.time() if now is None else now

        record = self._cache.get(code)
        if record is None:
            record = self._store.get(code)
            if record is None:
                return None
            self._cache.put(code, record)

        if record.is_expired(now):
            # Lazily evict stale/expired entries so neither the cache nor a
            # subsequent lookup can ever serve them again.
            self._cache.invalidate(code)
            return None

        self._store.record_click(code, now, referrer, user_agent)
        return record.long_url

    def delete_link(self, code: str) -> bool:
        """Delete a link and synchronously invalidate its cache entry.

        Returns True if a link existed and was deleted, False otherwise.
        """
        deleted = self._store.delete(code)
        self._cache.invalidate(code)
        return deleted

    def stats(self, code: str) -> Optional[dict]:
        record = self._store.get(code)
        if record is None:
            return None
        return {
            "code": code,
            "long_url": record.long_url,
            "created_at": record.created_at,
            "expires_at": record.expires_at,
            "clicks": self._store.click_count(code),
        }

    def short_url(self, code: str) -> str:
        return f"{self.base_url}/{code}"

    def _validate_url(self, url: str) -> None:
        if not url:
            raise InvalidURLError("url must not be empty")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise InvalidURLError("url must start with http:// or https://")
        if not parsed.netloc:
            raise InvalidURLError("url must include a host")
