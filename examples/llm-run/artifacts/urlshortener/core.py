"""Short code generation, validation, cache, rate limiting and async analytics."""
import re
import string
import threading
import time
import queue
from collections import OrderedDict
from datetime import datetime
from urllib.parse import urlparse

BASE62 = string.digits + string.ascii_lowercase + string.ascii_uppercase
CUSTOM_ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")


def encode_base62(num):
    if num <= 0:
        return BASE62[0]
    digits = []
    while num:
        num, rem = divmod(num, 62)
        digits.append(BASE62[rem])
    return "".join(reversed(digits))


def validate_long_url(url):
    if not isinstance(url, str) or len(url) > 4096:
        return False
    try:
        p = urlparse(url)
    except Exception:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc)


def validate_custom_alias(alias):
    return isinstance(alias, str) and bool(CUSTOM_ALIAS_RE.match(alias))


def validate_expiry(expires_at):
    if expires_at is None:
        return True
    try:
        datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        return True
    except Exception:
        return False


class LRUCache:
    def __init__(self, capacity=1000):
        self.capacity = capacity
        self.data = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            if key not in self.data:
                return None
            self.data.move_to_end(key)
            return self.data[key]

    def set(self, key, value):
        with self.lock:
            self.data[key] = value
            self.data.move_to_end(key)
            if len(self.data) > self.capacity:
                self.data.popitem(last=False)

    def invalidate(self, key):
        with self.lock:
            self.data.pop(key, None)


class TokenBucketLimiter:
    def __init__(self, rate=50.0, capacity=100.0):
        self.rate = rate
        self.capacity = capacity
        self.buckets = {}
        self.lock = threading.Lock()

    def allow(self, identifier):
        now = time.monotonic()
        with self.lock:
            tokens, last = self.buckets.get(identifier, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens < 1.0:
                self.buckets[identifier] = (tokens, now)
                return False
            tokens -= 1.0
            self.buckets[identifier] = (tokens, now)
            return True


class AnalyticsPipeline:
    """Buffers click events and flushes them in batches to storage."""

    def __init__(self, storage, flush_interval=1.0):
        self.storage = storage
        self.q = queue.Queue()
        self.flush_interval = flush_interval
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def record(self, short_code, clicked_at, referrer, user_agent, device, geo_country):
        self.q.put((short_code, clicked_at, referrer, user_agent, device, geo_country))

    def _drain(self):
        events = []
        try:
            while True:
                events.append(self.q.get_nowait())
        except queue.Empty:
            pass
        return events

    def _apply(self, events):
        if not events:
            return
        self.storage.insert_clicks_batch(events)
        daily = {}
        for e in events:
            day = e[1][:10]
            daily[(e[0], day)] = daily.get((e[0], day), 0) + 1
        for (code, day), cnt in daily.items():
            self.storage.upsert_daily_aggregate(code, day, cnt)

    def _run(self):
        while not self._stop.is_set():
            self._apply(self._drain())
            self._stop.wait(self.flush_interval)

    def flush_now(self):
        self._apply(self._drain())

    def stop(self):
        self._stop.set()
