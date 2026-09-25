"""Thread-safe, bounded LRU cache used to speed up hot read paths.

Kept intentionally tiny and dependency-free: it is a straightforward
``OrderedDict``-backed cache guarded by a single lock, which is more than
adequate for the demo's request volumes while remaining safe for concurrent
access from multiple worker threads.

Callers are responsible for expiry semantics (see ``ShortenerService.resolve``):
this cache only knows about presence/absence of a key, not about whether the
cached value has "gone stale" from a domain perspective.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Generic, Optional, TypeVar

K = TypeVar("K")
V = TypeVar("V")

DEFAULT_MAX_SIZE = 10_000


class LRUCache(Generic[K, V]):
    """A bounded least-recently-used cache safe for concurrent access."""

    def __init__(self, max_size: int = DEFAULT_MAX_SIZE) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        self._max_size = max_size
        self._data: "OrderedDict[K, V]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: K) -> Optional[V]:
        """Return the cached value for ``key``, or ``None`` on a miss."""
        with self._lock:
            if key not in self._data:
                return None
            value = self._data.pop(key)
            self._data[key] = value  # refresh recency
            return value

    def put(self, key: K, value: V) -> None:
        """Insert or refresh ``key``, evicting the least-recently-used entry if full."""
        with self._lock:
            if key in self._data:
                self._data.pop(key)
            self._data[key] = value
            if len(self._data) > self._max_size:
                self._data.popitem(last=False)

    def invalidate(self, key: K) -> None:
        """Remove ``key`` from the cache if present. Safe to call on a miss."""
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def __contains__(self, key: K) -> bool:
        with self._lock:
            return key in self._data
