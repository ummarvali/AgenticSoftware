# URL Shortener — Architecture

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
