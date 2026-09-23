"""Async click analytics: ingestion queue and stream aggregation."""
import threading
from collections import defaultdict, Counter
from dataclasses import dataclass


@dataclass
class ClickEvent:
    event_id: str
    short_code: str
    timestamp: str
    ip_hash: str
    user_agent: str
    referrer: str
    country: str
    device_type: str
    is_bot: bool = False


class AnalyticsQueue:
    """In-memory stand-in for a Kafka/Kinesis ingestion queue."""

    def __init__(self):
        self._items = []
        self._lock = threading.Lock()

    def push(self, event: ClickEvent):
        with self._lock:
            self._items.append(event)

    def drain(self):
        with self._lock:
            items, self._items = self._items, []
        return items


class AnalyticsProcessor:
    """Stream consumer aggregating click events by short_code/date."""

    def __init__(self, queue: AnalyticsQueue):
        self.queue = queue
        self._lock = threading.Lock()
        self._agg = defaultdict(lambda: defaultdict(lambda: {
            "clicks": 0, "visitors": set(), "referrers": Counter(), "countries": Counter()
        }))

    def process_all(self):
        for event in self.queue.drain():
            if event.is_bot:
                continue
            date = event.timestamp[:10]
            with self._lock:
                bucket = self._agg[event.short_code][date]
                bucket["clicks"] += 1
                bucket["visitors"].add(event.ip_hash)
                bucket["referrers"][event.referrer or "direct"] += 1
                bucket["countries"][event.country or "unknown"] += 1

    def get_analytics(self, short_code: str):
        self.process_all()
        dates = self._agg.get(short_code, {})
        total_clicks = sum(d["clicks"] for d in dates.values())
        unique = set()
        referrers = Counter()
        countries = Counter()
        by_date = []
        for date, d in sorted(dates.items()):
            by_date.append({"date": date, "clicks": d["clicks"]})
            unique |= d["visitors"]
            referrers.update(d["referrers"])
            countries.update(d["countries"])
        return {
            "short_code": short_code,
            "total_clicks": total_clicks,
            "unique_visitors": len(unique),
            "by_date": by_date,
            "top_referrers": [r for r, _ in referrers.most_common(5)],
            "top_countries": [c for c, _ in countries.most_common(5)],
        }
