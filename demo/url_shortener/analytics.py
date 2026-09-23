"""Read-side analytics computed from recorded click events."""

from __future__ import annotations

from collections import Counter

from .store import Store


class AnalyticsService:
    """Aggregates click events into simple, presentable metrics."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def total_clicks(self, code: str) -> int:
        return self._store.click_count(code)

    def top_referrers(self, code: str, limit: int = 5) -> list:
        counter = Counter(
            (click.get("referrer") or "direct") for click in self._store.clicks(code)
        )
        return counter.most_common(limit)

    def report(self, code: str) -> dict:
        clicks = self._store.clicks(code)
        return {
            "code": code,
            "total_clicks": len(clicks),
            "top_referrers": self.top_referrers(code),
        }
