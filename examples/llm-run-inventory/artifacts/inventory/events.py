"""In-memory pub/sub event bus and simple metrics counters.

Stands in for a future Kafka/SNS topic and a Prometheus-style metrics
endpoint, using only the standard library.
"""
import collections
import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger("inventory")


class Metrics:
    """Thread-safe in-memory counters exposed via /metrics."""

    def __init__(self):
        self._counters = collections.defaultdict(int)
        self._lock = threading.Lock()

    def inc(self, name, amount=1):
        with self._lock:
            self._counters[name] += amount

    def render(self):
        with self._lock:
            items = sorted(self._counters.items())
        return "\n".join(f"{k} {v}" for k, v in items) + "\n"


class EventBus:
    """Minimal publish/subscribe list simulating a message broker topic."""

    def __init__(self):
        self._subscribers = []
        self.events = []
        self._lock = threading.Lock()

    def subscribe(self, callback):
        self._subscribers.append(callback)

    def publish(self, topic, payload):
        event = {
            "topic": topic,
            "payload": payload,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self.events.append(event)
        for callback in self._subscribers:
            try:
                callback(event)
            except Exception:  # pragma: no cover - defensive
                logger.exception("event subscriber raised an error")
