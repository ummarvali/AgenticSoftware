"""URL-shortener knowledge pack — the mandatory use case.

This pack generates a **real, runnable, dependency-free** URL shortener:

* base62 code generation, in-memory *and* SQLite persistence, click analytics
* a WSGI HTTP API (shorten / redirect / stats / health)
* unit **and** integration tests written with :mod:`unittest`, so the validator can
  execute them offline with ``python -m unittest`` (no third-party runner needed)

Every string constant below is a file that will be written verbatim into the run's
artifact directory. They are kept as plain constants (no templating) so the emitted
code is exactly what you review here — no hidden substitution.
"""

from __future__ import annotations

from agentic_sdlc.knowledge.base import KnowledgePack
from agentic_sdlc.models import AnalysisResult, ApiEndpoint, Architecture, Artifact

# --------------------------------------------------------------------------- #
# Generated application source                                                #
# --------------------------------------------------------------------------- #

_INIT = '''"""A small, dependency-free URL shortener library and HTTP service."""

from .service import ShortenerService, InvalidURLError, AliasError
from .store import InMemoryStore, SqliteStore, LinkRecord
from .analytics import AnalyticsService
from . import base62

__all__ = [
    "ShortenerService",
    "InvalidURLError",
    "AliasError",
    "InMemoryStore",
    "SqliteStore",
    "LinkRecord",
    "AnalyticsService",
    "base62",
]
'''

_BASE62 = '''"""Base62 integer<->string codec used to turn a numeric id into a short slug.

Base62 (0-9, A-Z, a-z) packs the most information into URL-safe characters, so a
7-character code addresses ~3.5 trillion links while staying human-typeable.
"""

from __future__ import annotations

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
BASE = len(ALPHABET)
_INDEX = {ch: i for i, ch in enumerate(ALPHABET)}


def encode(number: int) -> str:
    """Encode a non-negative integer to a base62 string."""

    if number < 0:
        raise ValueError("only non-negative integers can be encoded")
    if number == 0:
        return ALPHABET[0]
    chars = []
    while number:
        number, remainder = divmod(number, BASE)
        chars.append(ALPHABET[remainder])
    return "".join(reversed(chars))


def decode(text: str) -> int:
    """Decode a base62 string back to its integer value."""

    if not text:
        raise ValueError("cannot decode an empty string")
    number = 0
    for ch in text:
        if ch not in _INDEX:
            raise ValueError(f"invalid base62 character: {ch!r}")
        number = number * BASE + _INDEX[ch]
    return number
'''

_STORE = '''"""Persistence layer for links and click events.

Two interchangeable backends implement the same :class:`Store` protocol:

* :class:`InMemoryStore` — zero-setup, used by tests and for local demos.
* :class:`SqliteStore` — durable storage on the Python standard library only.

Swapping backends changes durability without touching the service or API layers.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class LinkRecord:
    """A single shortened link."""

    code: str
    long_url: str
    created_at: float
    expires_at: Optional[float] = None

    def is_expired(self, now: Optional[float] = None) -> bool:
        if self.expires_at is None:
            return False
        now = time.time() if now is None else now
        return now >= self.expires_at


class Store(Protocol):
    """Storage contract shared by every backend."""

    def next_id(self) -> int: ...
    def create_link(self, record: LinkRecord) -> LinkRecord: ...
    def get(self, code: str) -> Optional[LinkRecord]: ...
    def find_by_url(self, long_url: str) -> Optional[LinkRecord]: ...
    def record_click(self, code: str, ts: float, referrer, user_agent) -> None: ...
    def click_count(self, code: str) -> int: ...
    def clicks(self, code: str) -> list: ...


class InMemoryStore:
    """Dictionary-backed store. Not durable; ideal for tests and demos."""

    def __init__(self) -> None:
        self._links: dict[str, LinkRecord] = {}
        self._by_url: dict[str, str] = {}
        self._clicks: dict[str, list] = {}
        self._counter = 0

    def next_id(self) -> int:
        self._counter += 1
        return self._counter

    def create_link(self, record: LinkRecord) -> LinkRecord:
        if record.code in self._links:
            raise KeyError(f"code already exists: {record.code}")
        self._links[record.code] = record
        self._by_url.setdefault(record.long_url, record.code)
        self._clicks.setdefault(record.code, [])
        return record

    def get(self, code: str) -> Optional[LinkRecord]:
        return self._links.get(code)

    def find_by_url(self, long_url: str) -> Optional[LinkRecord]:
        code = self._by_url.get(long_url)
        return self._links.get(code) if code else None

    def record_click(self, code: str, ts: float, referrer, user_agent) -> None:
        self._clicks.setdefault(code, []).append(
            {"ts": ts, "referrer": referrer, "user_agent": user_agent}
        )

    def click_count(self, code: str) -> int:
        return len(self._clicks.get(code, []))

    def clicks(self, code: str) -> list:
        return list(self._clicks.get(code, []))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS id_seq (id INTEGER PRIMARY KEY AUTOINCREMENT);
CREATE TABLE IF NOT EXISTS links (
    code       TEXT PRIMARY KEY,
    long_url   TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL
);
CREATE INDEX IF NOT EXISTS idx_links_url ON links(long_url);
CREATE TABLE IF NOT EXISTS clicks (
    code       TEXT NOT NULL,
    ts         REAL NOT NULL,
    referrer   TEXT,
    user_agent TEXT
);
CREATE INDEX IF NOT EXISTS idx_clicks_code ON clicks(code);
"""


class SqliteStore:
    """Durable store backed by SQLite (standard library, no external service)."""

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def next_id(self) -> int:
        cur = self._conn.execute("INSERT INTO id_seq DEFAULT VALUES")
        self._conn.commit()
        return int(cur.lastrowid)

    def create_link(self, record: LinkRecord) -> LinkRecord:
        self._conn.execute(
            "INSERT INTO links(code, long_url, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (record.code, record.long_url, record.created_at, record.expires_at),
        )
        self._conn.commit()
        return record

    def get(self, code: str) -> Optional[LinkRecord]:
        row = self._conn.execute("SELECT * FROM links WHERE code = ?", (code,)).fetchone()
        return self._to_record(row) if row else None

    def find_by_url(self, long_url: str) -> Optional[LinkRecord]:
        row = self._conn.execute(
            "SELECT * FROM links WHERE long_url = ? ORDER BY created_at ASC LIMIT 1",
            (long_url,),
        ).fetchone()
        return self._to_record(row) if row else None

    def record_click(self, code: str, ts: float, referrer, user_agent) -> None:
        self._conn.execute(
            "INSERT INTO clicks(code, ts, referrer, user_agent) VALUES (?, ?, ?, ?)",
            (code, ts, referrer, user_agent),
        )
        self._conn.commit()

    def click_count(self, code: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM clicks WHERE code = ?", (code,)
        ).fetchone()
        return int(row["n"])

    def clicks(self, code: str) -> list:
        rows = self._conn.execute(
            "SELECT ts, referrer, user_agent FROM clicks WHERE code = ?", (code,)
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _to_record(row: sqlite3.Row) -> LinkRecord:
        return LinkRecord(
            code=row["code"],
            long_url=row["long_url"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )
'''

_SERVICE = '''"""Core business logic: validate, shorten, resolve, and report on links."""

from __future__ import annotations

import re
import time
from typing import Optional
from urllib.parse import urlparse

from . import base62
from .store import InMemoryStore, LinkRecord, Store

# Offset so the smallest auto-generated code is already several characters long,
# which avoids trivially guessable one-character slugs.
CODE_OFFSET = 100_000
ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")


class InvalidURLError(ValueError):
    """Raised when a submitted URL is missing, malformed, or uses a bad scheme."""


class AliasError(ValueError):
    """Raised when a custom alias is invalid or already taken."""


class ShortenerService:
    """Framework-agnostic core. The API layer is a thin adapter over this class."""

    def __init__(self, store: Optional[Store] = None, base_url: str = "http://localhost:8000") -> None:
        self._store: Store = store or InMemoryStore()
        self.base_url = base_url.rstrip("/")

    @property
    def store(self) -> Store:
        return self._store

    def shorten(
        self,
        long_url: str,
        custom_alias: Optional[str] = None,
        ttl_seconds: Optional[float] = None,
    ) -> LinkRecord:
        long_url = (long_url or "").strip()
        self._validate_url(long_url)
        now = time.time()
        expires_at = now + ttl_seconds if ttl_seconds is not None else None

        if custom_alias:
            if not ALIAS_RE.match(custom_alias):
                raise AliasError("alias must be 3-32 chars from [A-Za-z0-9_-]")
            if self._store.get(custom_alias) is not None:
                raise AliasError(f"alias '{custom_alias}' is already taken")
            record = LinkRecord(custom_alias, long_url, now, expires_at)
            return self._store.create_link(record)

        # Idempotency: an identical, still-valid URL returns its existing code so
        # we never mint duplicate slugs for the same destination.
        existing = self._store.find_by_url(long_url)
        if existing is not None and not existing.is_expired(now):
            return existing

        new_id = self._store.next_id()
        code = base62.encode(new_id + CODE_OFFSET)
        record = LinkRecord(code, long_url, now, expires_at)
        return self._store.create_link(record)

    def resolve(
        self,
        code: str,
        *,
        referrer: Optional[str] = None,
        user_agent: Optional[str] = None,
        now: Optional[float] = None,
    ) -> Optional[str]:
        record = self._store.get(code)
        if record is None:
            return None
        now = time.time() if now is None else now
        if record.is_expired(now):
            return None
        self._store.record_click(code, now, referrer, user_agent)
        return record.long_url

    def stats(self, code: str) -> Optional[dict]:
        record = self._store.get(code)
        if record is None:
            return None
        return {
            "code": code,
            "long_url": record.long_url,
            "created_at": record.created_at,
            "expires_at": record.expires_at,
            "clicks": self._store.click_count(code),
        }

    def short_url(self, code: str) -> str:
        return f"{self.base_url}/{code}"

    def _validate_url(self, url: str) -> None:
        if not url:
            raise InvalidURLError("url must not be empty")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise InvalidURLError("url must start with http:// or https://")
        if not parsed.netloc:
            raise InvalidURLError("url must include a host")
'''

_ANALYTICS = '''"""Read-side analytics computed from recorded click events."""

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
'''

_API = '''"""WSGI HTTP adapter over :class:`ShortenerService`.

Kept as a plain WSGI callable so it runs on the standard-library server and is
trivially testable without booting a socket (see ``tests/test_api.py``).
"""

from __future__ import annotations

import json

from .analytics import AnalyticsService
from .service import AliasError, InvalidURLError, ShortenerService

_REASON = {
    200: "OK",
    201: "Created",
    302: "Found",
    400: "Bad Request",
    404: "Not Found",
    429: "Too Many Requests",
    500: "Internal Server Error",
}


class WSGIApp:
    """Minimal router mapping HTTP requests onto the service.

    ``limiter`` is optional: any object with ``allow(key) -> bool`` guards link
    creation per client address (HTTP 429 when it says no)."""

    def __init__(self, service: ShortenerService | None = None, limiter=None) -> None:
        self.service = service or ShortenerService()
        self.analytics = AnalyticsService(self.service.store)
        self.limiter = limiter

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")
        try:
            if path == "/healthz":
                return self._json(start_response, 200, {"status": "ok"})
            if path == "/api/shorten" and method == "POST":
                if self.limiter and not self.limiter.allow(environ.get("REMOTE_ADDR", "?")):
                    return self._json(start_response, 429, {"error": "rate limit exceeded"})
                return self._shorten(environ, start_response)
            if path.startswith("/api/stats/") and method == "GET":
                return self._stats(start_response, path[len("/api/stats/") :])
            if method == "GET" and path != "/" and "/" not in path[1:]:
                return self._redirect(environ, start_response, path[1:])
            return self._json(start_response, 404, {"error": "not found"})
        except (InvalidURLError, AliasError) as exc:
            return self._json(start_response, 400, {"error": str(exc)})
        except Exception:  # defensive: never leak internals to the client
            return self._json(start_response, 500, {"error": "internal error"})

    def _shorten(self, environ, start_response):
        try:
            data = json.loads(self._read_body(environ) or "{}")
        except json.JSONDecodeError:
            return self._json(start_response, 400, {"error": "invalid JSON body"})
        record = self.service.shorten(
            data.get("url"),
            custom_alias=data.get("alias"),
            ttl_seconds=data.get("ttl_seconds"),
        )
        return self._json(
            start_response,
            201,
            {
                "code": record.code,
                "short_url": self.service.short_url(record.code),
                "long_url": record.long_url,
                "expires_at": record.expires_at,
            },
        )

    def _redirect(self, environ, start_response, code):
        long_url = self.service.resolve(
            code,
            referrer=environ.get("HTTP_REFERER"),
            user_agent=environ.get("HTTP_USER_AGENT"),
        )
        if long_url is None:
            return self._json(start_response, 404, {"error": "unknown or expired code"})
        start_response("302 Found", [("Location", long_url), ("Content-Length", "0")])
        return [b""]

    def _stats(self, start_response, code):
        stats = self.service.stats(code)
        if stats is None:
            return self._json(start_response, 404, {"error": "unknown code"})
        stats["analytics"] = self.analytics.report(code)
        return self._json(start_response, 200, stats)

    @staticmethod
    def _read_body(environ) -> str:
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return ""
        return environ["wsgi.input"].read(length).decode("utf-8")

    def _json(self, start_response, status, payload):
        body = json.dumps(payload).encode("utf-8")
        start_response(
            f"{status} {_REASON.get(status, 'OK')}",
            [("Content-Type", "application/json"), ("Content-Length", str(len(body)))],
        )
        return [body]


app = WSGIApp()
'''

_SERVER = '''"""Run the URL shortener on the standard-library WSGI server.

Configuration comes from the environment (see ``config.py``) so the same entrypoint
works on a laptop and in a container: ``SHORTENER_STORE=sqlite`` with
``SHORTENER_DB_PATH`` selects the durable backend; the default is in-memory.
"""

from __future__ import annotations

from wsgiref.simple_server import make_server

from . import config
from .api import WSGIApp
from .service import ShortenerService
from .store import InMemoryStore, SqliteStore
#LIMITER_IMPORT


def build_app() -> WSGIApp:
    store = SqliteStore(config.DB_PATH) if config.STORE_BACKEND == "sqlite" else InMemoryStore()
    service = ShortenerService(store=store, base_url=config.BASE_URL)
    return WSGIApp(service=service#LIMITER_ARG)


def main(host: str | None = None, port: int | None = None) -> None:
    host = host or config.HOST
    port = port or config.PORT
    with make_server(host, port, build_app()) as httpd:
        print(f"URL shortener listening on http://{host}:{port} (store={config.STORE_BACKEND})")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
'''

_RATELIMIT = '''"""Per-client token-bucket rate limiter for link creation.

Each client key (the remote address) gets a bucket of ``burst`` tokens refilled at
``per_minute / 60`` tokens per second. In-process and thread-safe: a multi-instance
deployment would move the buckets to a shared store (e.g. Redis) or the API gateway.
"""

from __future__ import annotations

import os
import threading
import time


class TokenBucketLimiter:
    def __init__(self, per_minute: int | None = None, burst: int | None = None, clock=time.monotonic) -> None:
        self.per_minute = per_minute or int(os.environ.get("SHORTENER_RATE_LIMIT_PER_MINUTE", "60"))
        self.burst = burst or self.per_minute
        self._rate = self.per_minute / 60.0
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self.burst), now))
            tokens = min(float(self.burst), tokens + (now - last) * self._rate)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1.0, now)
            return True
'''

_TEST_RATELIMIT = '''"""Rate limiting: the bucket itself, and the API returning 429."""

import io
import json
import unittest

from url_shortener.api import WSGIApp
from url_shortener.ratelimit import TokenBucketLimiter


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class TestTokenBucket(unittest.TestCase):
    def test_burst_then_block_then_refill(self):
        clock = FakeClock()
        rl = TokenBucketLimiter(per_minute=60, burst=2, clock=clock)
        self.assertTrue(rl.allow("a"))
        self.assertTrue(rl.allow("a"))
        self.assertFalse(rl.allow("a"))
        self.assertTrue(rl.allow("b"))          # buckets are per client
        clock.t += 1.0                          # 60/min refills one token per second
        self.assertTrue(rl.allow("a"))


class TestApiRateLimit(unittest.TestCase):
    def _post(self, app):
        body = json.dumps({"url": "https://example.com"}).encode()
        env = {"REQUEST_METHOD": "POST", "PATH_INFO": "/api/shorten", "REMOTE_ADDR": "10.0.0.1",
               "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
        status = {}
        app(env, lambda s, h: status.setdefault("s", s))
        return status["s"]

    def test_shorten_returns_429_when_limited(self):
        app = WSGIApp(limiter=TokenBucketLimiter(per_minute=60, burst=1, clock=FakeClock()))
        self.assertTrue(self._post(app).startswith("201"))
        self.assertTrue(self._post(app).startswith("429"))


if __name__ == "__main__":
    unittest.main()
'''

_CONFIG = '''"""Environment-driven configuration for the URL shortener."""

from __future__ import annotations

import os

BASE_URL = os.environ.get("SHORTENER_BASE_URL", "http://localhost:8000")
STORE_BACKEND = os.environ.get("SHORTENER_STORE", "memory")  # "memory" | "sqlite"
DB_PATH = os.environ.get("SHORTENER_DB_PATH", "shortener.db")
HOST = os.environ.get("SHORTENER_HOST", "127.0.0.1")
PORT = int(os.environ.get("SHORTENER_PORT", "8000"))
'''

# --------------------------------------------------------------------------- #
# Generated tests (unittest so the validator needs no third-party runner)     #
# --------------------------------------------------------------------------- #

_TEST_BASE62 = '''"""Unit tests for the base62 codec."""

import unittest

from url_shortener import base62


class Base62Tests(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(base62.encode(0), "0")

    def test_round_trip(self):
        for n in [1, 61, 62, 63, 12345, 999999, 2 ** 32]:
            self.assertEqual(base62.decode(base62.encode(n)), n)

    def test_negative_rejected(self):
        with self.assertRaises(ValueError):
            base62.encode(-1)

    def test_invalid_char_rejected(self):
        with self.assertRaises(ValueError):
            base62.decode("!!")


if __name__ == "__main__":
    unittest.main()
'''

_TEST_SERVICE = '''"""Unit tests for the shortener service and both storage backends."""

import unittest

from url_shortener.service import AliasError, InvalidURLError, ShortenerService
from url_shortener.store import InMemoryStore, SqliteStore


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.svc = ShortenerService(store=InMemoryStore(), base_url="http://sho.rt")

    def test_shorten_and_resolve(self):
        rec = self.svc.shorten("https://example.com/page")
        self.assertTrue(rec.code)
        self.assertEqual(self.svc.resolve(rec.code), "https://example.com/page")

    def test_short_url_format(self):
        rec = self.svc.shorten("https://example.com/x")
        self.assertEqual(self.svc.short_url(rec.code), f"http://sho.rt/{rec.code}")

    def test_idempotent_for_same_url(self):
        a = self.svc.shorten("https://example.com")
        b = self.svc.shorten("https://example.com")
        self.assertEqual(a.code, b.code)

    def test_custom_alias(self):
        rec = self.svc.shorten("https://example.com", custom_alias="promo")
        self.assertEqual(rec.code, "promo")
        self.assertEqual(self.svc.resolve("promo"), "https://example.com")

    def test_duplicate_alias_rejected(self):
        self.svc.shorten("https://a.com", custom_alias="dup")
        with self.assertRaises(AliasError):
            self.svc.shorten("https://b.com", custom_alias="dup")

    def test_bad_alias_rejected(self):
        with self.assertRaises(AliasError):
            self.svc.shorten("https://a.com", custom_alias="no spaces!")

    def test_invalid_url_rejected(self):
        for bad in ["", "ftp://x", "notaurl", "javascript:alert(1)"]:
            with self.assertRaises(InvalidURLError):
                self.svc.shorten(bad)

    def test_expiry(self):
        rec = self.svc.shorten("https://example.com/expired", ttl_seconds=-1)
        self.assertIsNone(self.svc.resolve(rec.code))

    def test_unknown_code(self):
        self.assertIsNone(self.svc.resolve("missing"))

    def test_stats_counts_clicks(self):
        rec = self.svc.shorten("https://example.com/y")
        self.svc.resolve(rec.code)
        self.svc.resolve(rec.code)
        self.assertEqual(self.svc.stats(rec.code)["clicks"], 2)

    def test_sqlite_backend(self):
        svc = ShortenerService(store=SqliteStore(":memory:"))
        rec = svc.shorten("https://example.com/db")
        self.assertEqual(svc.resolve(rec.code), "https://example.com/db")
        self.assertEqual(svc.stats(rec.code)["clicks"], 1)


if __name__ == "__main__":
    unittest.main()
'''

_TEST_API = '''"""Integration tests driving the WSGI app end to end (no socket needed)."""

import io
import json
import unittest

from url_shortener.api import WSGIApp


def call(app, method, path, body=None):
    raw = (body or "").encode("utf-8")
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "wsgi.input": io.BytesIO(raw),
        "CONTENT_LENGTH": str(len(raw)),
    }
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    payload = b"".join(app(environ, start_response))
    return int(captured["status"].split()[0]), captured["headers"], payload


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.app = WSGIApp()

    def test_health(self):
        status, _, _ = call(self.app, "GET", "/healthz")
        self.assertEqual(status, 200)

    def test_shorten_then_redirect(self):
        status, _, body = call(
            self.app, "POST", "/api/shorten", json.dumps({"url": "https://example.com/a"})
        )
        self.assertEqual(status, 201)
        code = json.loads(body)["code"]
        status, headers, _ = call(self.app, "GET", "/" + code)
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "https://example.com/a")

    def test_stats(self):
        _, _, body = call(
            self.app, "POST", "/api/shorten", json.dumps({"url": "https://example.com/b"})
        )
        code = json.loads(body)["code"]
        call(self.app, "GET", "/" + code)
        status, _, body = call(self.app, "GET", "/api/stats/" + code)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["clicks"], 1)

    def test_bad_url_returns_400(self):
        status, _, _ = call(
            self.app, "POST", "/api/shorten", json.dumps({"url": "ftp://x"})
        )
        self.assertEqual(status, 400)

    def test_unknown_code_returns_404(self):
        status, _, _ = call(self.app, "GET", "/nope")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
'''

# --------------------------------------------------------------------------- #
# Generated contract & documentation                                          #
# --------------------------------------------------------------------------- #

_OPENAPI = '''openapi: 3.0.3
info:
  title: URL Shortener API
  version: 1.0.0
  description: Shorten URLs, redirect, and read click analytics.
servers:
  - url: http://localhost:8000
paths:
  /api/shorten:
    post:
      summary: Create a short link
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [url]
              properties:
                url: { type: string, format: uri, example: "https://example.com/page" }
                alias: { type: string, example: "promo" }
                ttl_seconds: { type: integer, example: 3600 }
      responses:
        "201":
          description: Short link created
          content:
            application/json:
              schema:
                type: object
                properties:
                  code: { type: string }
                  short_url: { type: string }
                  long_url: { type: string }
                  expires_at: { type: number, nullable: true }
        "400": { description: Invalid URL or alias }
  /{code}:
    get:
      summary: Redirect to the original URL
      parameters:
        - in: path
          name: code
          required: true
          schema: { type: string }
      responses:
        "302": { description: Redirect to the long URL }
        "404": { description: Unknown or expired code }
  /api/stats/{code}:
    get:
      summary: Click analytics for a code
      parameters:
        - in: path
          name: code
          required: true
          schema: { type: string }
      responses:
        "200":
          description: Stats payload
          content:
            application/json:
              schema:
                type: object
                properties:
                  code: { type: string }
                  long_url: { type: string }
                  clicks: { type: integer }
        "404": { description: Unknown code }
  /healthz:
    get:
      summary: Liveness probe
      responses:
        "200": { description: Service is up }
'''

_GEN_README = '''# URL Shortener Service

A single-process, dependency-free URL shortener with a REST API, pluggable persistence
(in-memory or SQLite) and click analytics — structured so each layer can scale out
independently. Generated by the Agentic SDLC system.

## Run it

```bash
python -m url_shortener.server        # serves on http://127.0.0.1:8000 (in-memory store)
SHORTENER_STORE=sqlite SHORTENER_DB_PATH=shortener.db python -m url_shortener.server   # durable
```

Environment: `SHORTENER_HOST`, `SHORTENER_PORT`, `SHORTENER_BASE_URL`, `SHORTENER_STORE`
(`memory` | `sqlite`), `SHORTENER_DB_PATH`.

## Try it

```bash
curl -X POST http://127.0.0.1:8000/api/shorten \\
  -H "Content-Type: application/json" \\
  -d '{"url": "https://example.com/some/long/path"}'
# -> {"code": "...", "short_url": "http://.../<code>", ...}

curl -i http://127.0.0.1:8000/<code>          # 302 redirect
curl http://127.0.0.1:8000/api/stats/<code>   # click analytics
```

## Test it

```bash
python -m unittest discover -s tests -v
```

## Layout

| Path | Responsibility |
| --- | --- |
| `url_shortener/base62.py` | id <-> slug codec |
| `url_shortener/store.py` | in-memory + SQLite backends |
| `url_shortener/service.py` | validation, shorten, resolve, stats |
| `url_shortener/analytics.py` | click aggregation |
| `url_shortener/api.py` | WSGI HTTP adapter |
| `url_shortener/server.py` | dev server entrypoint |
| `openapi.yaml` | API contract |
'''

_GEN_ARCH = '''# URL Shortener — Architecture

## Layers

1. **API (`api.py`)** — WSGI adapter. Parses requests, maps errors to status codes,
   never leaks internals. Swappable for FastAPI/Flask without touching core logic.
2. **Service (`service.py`)** — framework-agnostic business rules: URL validation,
   idempotent shorten, alias handling, expiry, resolve, stats.
3. **Store (`store.py`)** — `Store` protocol with in-memory and SQLite backends.
4. **Analytics (`analytics.py`)** — read-side aggregation over click events.

## Data model

- `links(code PK, long_url, created_at, expires_at)`
- `clicks(code FK, ts, referrer, user_agent)`

## Key decisions

- **Base62 over sequential id** — short, URL-safe, dense codes.
- **Offset (100k)** — avoids trivially short/guessable slugs.
- **Idempotent shorten** — identical live URLs reuse a code, preventing slug sprawl.
- **Protocol-based store** — durability is a deployment choice, not a code change.

## Scaling path

- Read-heavy: front redirects with a cache (code -> long_url).
- Write scale: replace `id_seq` with a sharded/range id allocator.
- Analytics: stream click events to a queue and aggregate out-of-band.
'''


class UrlShortenerPack(KnowledgePack):
    """Knowledge pack for the mandatory URL-shortener use case."""

    domain = "url_shortener"

    _KEYWORDS = ("url shortener", "url-shortener", "shorten url", "short link", "tinyurl", "link shortener")

    @classmethod
    def matches(cls, text: str) -> bool:
        low = text.lower()
        if any(k in low for k in cls._KEYWORDS):
            return True
        # Also match the looser "shorten ... url/link" phrasing.
        return "shorten" in low and ("url" in low or "link" in low)

    def functional_requirements(self) -> list[str]:
        return [
            "Create a short code for a submitted long URL via POST /api/shorten.",
            "Support optional custom aliases and optional time-to-live (expiry).",
            "Redirect GET /{code} to the original URL with HTTP 302.",
            "Return per-code click analytics via GET /api/stats/{code}.",
            "Reject non-http(s) or malformed URLs with a 400 error.",
        ]

    def non_functional_requirements(self) -> list[str]:
        return [
            "Horizontal scalability: stateless API over a shared datastore.",
            "Low-latency redirects (O(1) code lookup).",
            "Durability option via SQLite backend; swappable persistence.",
            "Security: input validation, no scheme injection, no internal error leakage.",
        ]

    def architecture(self, analysis: AnalysisResult) -> Architecture:
        return Architecture(
            overview=(
                "A layered, stateless URL shortener: a thin WSGI API over a "
                "framework-agnostic service, a protocol-based persistence layer "
                "(in-memory or SQLite), and a read-side analytics aggregator."
            ),
            components=[
                "API layer (WSGI) — routing, (de)serialization, error mapping",
                "Service layer — validation, shorten/resolve/stats business rules",
                "Persistence layer — Store protocol with InMemory + SQLite backends",
                "Analytics layer — click-event aggregation",
                "Base62 codec — numeric id to URL-safe slug",
            ],
            data_model=[
                "links(code PK, long_url, created_at, expires_at)",
                "clicks(code, ts, referrer, user_agent)",
                "id_seq(id AUTOINCREMENT) — monotonic id source for base62 codes",
            ],
            api=[
                ApiEndpoint("POST", "/api/shorten", "Create a short link",
                            request="{url, alias?, ttl_seconds?}",
                            response="{code, short_url, long_url, expires_at}", status=201),
                ApiEndpoint("GET", "/{code}", "Redirect to the long URL",
                            response="302 Location: <long_url>", status=302),
                ApiEndpoint("GET", "/api/stats/{code}", "Click analytics for a code",
                            response="{code, long_url, clicks, analytics}", status=200),
                ApiEndpoint("GET", "/healthz", "Liveness probe",
                            response="{status: ok}", status=200),
            ],
            decisions=([
                "Per-client token bucket on POST /api/shorten (HTTP 429 when exhausted), "
                "wired in at the server so the API core stays policy-free.",
            ] if self._wants_rate_limit(analysis) else []) + [
                "Base62 encoding of an offset numeric id for short, dense, URL-safe codes.",
                "Idempotent shorten: identical live URLs reuse their code.",
                "Store as a Protocol so durability is a deployment choice, not a rewrite.",
                "WSGI core so the service is server- and framework-agnostic and unit-testable.",
            ],
            tradeoffs=[
                "In-memory store is fastest but non-durable; SQLite adds durability at I/O cost.",
                "Sequential-id base62 codes are predictable; a hash/random scheme trades "
                "guessability for a small collision-handling cost.",
                "Synchronous click recording is simplest; high write volume would move "
                "analytics to an async event pipeline.",
            ] + ([
                "In-process rate-limit buckets are exact for one instance but not shared; "
                "a fleet needs a shared counter store or gateway-level limiting.",
            ] if self._wants_rate_limit(analysis) else []),
        )

    @staticmethod
    def _wants_rate_limit(analysis: AnalysisResult) -> bool:
        text = " ".join([analysis.intent, analysis.normalized_problem,
                         *analysis.functional_requirements]).lower()
        return "rate limit" in text or "rate-limit" in text or "throttl" in text

    def change(self, analysis: AnalysisResult, repo_files: dict[str, str]) -> tuple[list[Artifact], str]:
        """Offline brownfield: add rate limiting to a repository that has this pack's own
        layout (e.g. ``demo/``). Any other change, or an unfamiliar layout, returns an
        empty change set — authoring arbitrary changes needs the live model."""

        plain_server = _SERVER.replace("#LIMITER_IMPORT\n", "").replace("#LIMITER_ARG", "")
        if not (self._wants_rate_limit(analysis)
                and repo_files.get("url_shortener/server.py") == plain_server
                and "url_shortener/api.py" in repo_files and "limiter" in repo_files["url_shortener/api.py"]):
            return [], ""
        files = [a for a in self.code(analysis, self.architecture(analysis))
                 if a.path in ("url_shortener/ratelimit.py", "url_shortener/server.py", "openapi.yaml")]
        files.append(Artifact("tests/test_ratelimit.py", _TEST_RATELIMIT, "test"))
        return files, ("Adds a per-client token-bucket limiter (url_shortener/ratelimit.py), wires it "
                       "into the server for POST /api/shorten (HTTP 429 when exhausted), documents the "
                       "429 in openapi.yaml, and adds tests for the bucket and the 429 path.")

    def code(self, analysis: AnalysisResult, architecture: Architecture) -> list[Artifact]:
        limited = self._wants_rate_limit(analysis)
        server = (_SERVER.replace("#LIMITER_IMPORT", "from .ratelimit import TokenBucketLimiter")
                         .replace("#LIMITER_ARG", ", limiter=TokenBucketLimiter()")
                  if limited else
                  _SERVER.replace("#LIMITER_IMPORT\n", "").replace("#LIMITER_ARG", ""))
        contract = (_OPENAPI.replace(
                        '        "400": { description: Invalid URL or alias }\n',
                        '        "400": { description: Invalid URL or alias }\n'
                        '        "429": { description: Rate limit exceeded for this client }\n', 1)
                    if limited else _OPENAPI)
        files = [
            Artifact("url_shortener/__init__.py", _INIT, "code"),
            Artifact("url_shortener/base62.py", _BASE62, "code"),
            Artifact("url_shortener/store.py", _STORE, "code"),
            Artifact("url_shortener/service.py", _SERVICE, "code"),
            Artifact("url_shortener/analytics.py", _ANALYTICS, "code"),
            Artifact("url_shortener/api.py", _API, "code"),
            Artifact("url_shortener/server.py", server, "code"),
            Artifact("url_shortener/config.py", _CONFIG, "config"),
            Artifact("openapi.yaml", contract, "contract"),
        ]
        if limited:
            files.insert(6, Artifact("url_shortener/ratelimit.py", _RATELIMIT, "code"))
        return files

    def tests(
        self,
        analysis: AnalysisResult,
        architecture: Architecture,
        code: list[Artifact],
    ) -> list[Artifact]:
        files = [
            Artifact("tests/test_base62.py", _TEST_BASE62, "test"),
            Artifact("tests/test_service.py", _TEST_SERVICE, "test"),
            Artifact("tests/test_api.py", _TEST_API, "test"),
        ]
        if self._wants_rate_limit(analysis):
            files.append(Artifact("tests/test_ratelimit.py", _TEST_RATELIMIT, "test"))
        return files

    def docs(self, analysis: AnalysisResult, architecture: Architecture) -> list[Artifact]:
        return [
            Artifact("README.md", _GEN_README, "docs"),
            Artifact("docs/ARCHITECTURE.md", _GEN_ARCH, "docs"),
        ]
