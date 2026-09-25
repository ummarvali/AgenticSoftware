"""Asynchronous analytics ingestion.

Click events are pushed onto an in-memory queue by the redirect path (fast,
non-blocking) and drained by a background worker that persists them and
maintains aggregated per-code stats. A separate scheduler thread expires
stale URLs and purges analytics data past the retention window.
"""
import threading
from collections import deque, namedtuple
from datetime import datetime, timezone, timedelta

ClickEvent = namedtuple("ClickEvent", ["short_code", "ts", "referrer", "user_agent", "ip", "geo_country"])

RETENTION_DAYS = 365


class AnalyticsQueue:
    """Thread-safe FIFO queue of pending click events."""

    def __init__(self):
        self._dq = deque()
        self._lock = threading.Lock()

    def push(self, event):
        with self._lock:
            self._dq.append(event)

    def drain(self, max_items=500):
        items = []
        with self._lock:
            for _ in range(min(max_items, len(self._dq))):
                items.append(self._dq.popleft())
        return items


def _apply_events(db, events):
    for ev in events:
        db.execute(
            "INSERT INTO click_events (short_code, ts, referrer, user_agent, ip, geo_country) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ev.short_code, ev.ts, ev.referrer, ev.user_agent, ev.ip, ev.geo_country),
        )
        row = db.query_one("SELECT total_clicks FROM url_stats WHERE short_code = ?", (ev.short_code,))
        if row:
            db.execute(
                "UPDATE url_stats SET total_clicks = total_clicks + 1, last_click_at = ? WHERE short_code = ?",
                (ev.ts, ev.short_code),
            )
        else:
            db.execute(
                "INSERT INTO url_stats (short_code, total_clicks, last_click_at) VALUES (?, 1, ?)",
                (ev.short_code, ev.ts),
            )


def flush(db, queue, max_items=500):
    """Drain up to max_items events from the queue and persist them. Returns count processed."""
    events = queue.drain(max_items)
    _apply_events(db, events)
    return len(events)


def analytics_worker(db, queue, stop_event, interval=0.05):
    """Background loop draining the analytics queue until stop_event is set."""
    while not stop_event.is_set():
        flush(db, queue)
        stop_event.wait(interval)
    flush(db, queue, max_items=1_000_000)  # final drain on shutdown


def scheduler_worker(db, stop_event, interval=5):
    """Periodically deactivate expired URLs and purge analytics past the retention window."""
    while not stop_event.is_set():
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "UPDATE urls SET active = 0 WHERE expires_at IS NOT NULL AND expires_at < ? AND active = 1",
            (now,),
        )
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
        db.execute("DELETE FROM click_events WHERE ts < ?", (cutoff,))
        stop_event.wait(interval)
