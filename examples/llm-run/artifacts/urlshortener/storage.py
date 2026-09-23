"""In-memory durable store, cache layer, and base62 ID generator."""
import threading
from dataclasses import dataclass
from typing import Optional

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def encode_base62(num: int) -> str:
    if num == 0:
        return _ALPHABET[0]
    digits = []
    base = len(_ALPHABET)
    while num:
        num, rem = divmod(num, base)
        digits.append(_ALPHABET[rem])
    return "".join(reversed(digits))


@dataclass
class UrlMapping:
    short_code: str
    long_url: str
    created_at: str
    updated_at: str
    expires_at: Optional[str] = None
    owner_id: Optional[str] = None
    is_active: bool = True
    custom_alias: bool = False
    click_count_cache: int = 0


class IdGenerator:
    """Snowflake-like sequential id generator, base62 encoded."""

    def __init__(self, start: int = 1000):
        self._counter = start
        self._lock = threading.Lock()

    def next_code(self) -> str:
        with self._lock:
            self._counter += 1
            n = self._counter
        return encode_base62(n)


class MemoryStore:
    """Durable (in-process) key-value store standing in for a sharded DB."""

    def __init__(self):
        self._data = {}
        self._lock = threading.RLock()

    def put(self, mapping: UrlMapping):
        with self._lock:
            self._data[mapping.short_code] = mapping

    def get(self, code: str):
        with self._lock:
            return self._data.get(code)

    def delete(self, code: str) -> bool:
        with self._lock:
            return self._data.pop(code, None) is not None


class Cache:
    """Read-through / write-through cache in front of the MemoryStore."""

    def __init__(self, store: MemoryStore):
        self.store = store
        self._cache = {}
        self._lock = threading.RLock()

    def get(self, code: str):
        with self._lock:
            if code in self._cache:
                return self._cache[code]
        mapping = self.store.get(code)
        if mapping is not None:
            with self._lock:
                self._cache[code] = mapping
        return mapping

    def put(self, code: str, mapping: UrlMapping):
        self.store.put(mapping)
        with self._lock:
            self._cache[code] = mapping

    def invalidate(self, code: str):
        with self._lock:
            self._cache.pop(code, None)
