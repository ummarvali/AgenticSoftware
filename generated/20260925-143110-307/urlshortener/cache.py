"""In-process LRU cache used to serve hot redirect lookups without hitting SQLite."""
import threading
from collections import OrderedDict


class LRUCache:
    """Thread-safe least-recently-used cache with a fixed capacity."""

    def __init__(self, capacity):
        self.capacity = max(1, capacity)
        self._data = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key not in self._data:
                return None
            self._data.move_to_end(key)
            return self._data[key]

    def put(self, key, value):
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            while len(self._data) > self.capacity:
                self._data.popitem(last=False)

    def evict(self, key):
        with self._lock:
            self._data.pop(key, None)

    def __len__(self):
        with self._lock:
            return len(self._data)
