"""The concrete workload the toolkit profiles: the url_shortener demo app.

Each call to :meth:`DemoWorkload.run_once` exercises the same code path a
real client would: create a short link, resolve it (recording a click), and
read its stats — covering both the backend/API logic and the SQLite storage
layer, which is where the design's assumed bottlenecks live.
"""

from __future__ import annotations

from url_shortener.service import ShortenerService
from url_shortener.store import SqliteStore


class DemoWorkload:
    """Wraps a fresh instance of the demo app for repeatable profiling/benchmarking."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.store = SqliteStore(db_path)
        self.service = ShortenerService(store=self.store, base_url="http://sho.rt")
        self._seed = 0

    def run_once(self) -> None:
        self._seed += 1
        url = f"https://example.com/page/{self._seed}"
        record = self.service.shorten(url)
        self.service.resolve(record.code)
        self.service.stats(record.code)

    def reset(self) -> None:
        self.store = SqliteStore(":memory:")
        self.service = ShortenerService(store=self.store, base_url="http://sho.rt")
        self._seed = 0
