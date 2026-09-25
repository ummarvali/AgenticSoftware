"""In-memory LRU cache standing in for a distributed cache (e.g. Redis)
in front of SQLite reads on the hot redirect path.
"""

import threading
from collections import OrderedDict


class LRUCache:
    def __init__(self, capacity=2048):
        self.capacity = capacity
        self._data = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key not in self._data:
                return None
            self._data.move_to_end(key)
            return self._data[key]

    def set(self, key, value):
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            if len(self._data) > self.capacity:
                self._data.popitem(last=False)

    def invalidate(self, key):
        with self._lock:
            self._data.pop(key, None)

    def clear(self):
        with self._lock:
            self._data.clear()

