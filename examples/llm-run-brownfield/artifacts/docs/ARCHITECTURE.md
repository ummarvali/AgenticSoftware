# URL Shortener — Architecture

## Layers

1. **API (`api.py`)** — WSGI adapter. Parses requests, maps errors to status codes,
   never leaks internals. Swappable for FastAPI/Flask without touching core logic.
2. **Service (`service.py`)** — framework-agnostic business rules: URL validation,
   idempotent shorten, alias handling, expiry, resolve, stats.
3. **Store (`store.py`)** — `Store` protocol with in-memory and SQLite backends.
4. **Analytics (`analytics.py`)** — read-side aggregation over click events.
5. **Rate limiting (`ratelimit.py`)** — sliding-window request budgets per client/rule,
   applied in the API layer before a handler runs.

## Data model

- `links(code PK, long_url, created_at, expires_at)`
- `clicks(code FK, ts, referrer, user_agent)`

## Key decisions

- **Base62 over sequential id** — short, URL-safe, dense codes.
- **Offset (100k)** — avoids trivially short/guessable slugs.
- **Idempotent shorten** — identical live URLs reuse a code, preventing slug sprawl.
- **Protocol-based store** — durability is a deployment choice, not a code change.

## Rate limiting

`WSGIApp` checks a `RateLimiter` before dispatching to a handler, keyed by
`(rule_name, client_id)`:

- **Client identification** — by `REMOTE_ADDR`; `X-Forwarded-For` is only honoured when
  the peer address is listed in `SHORTENER_TRUSTED_PROXIES`, to avoid trivial spoofing when
  no proxy is in front of the service.
- **Rules** — named budgets (`shorten`, `redirect`, `admin`), each a `(limit, window_seconds)`
  pair, loaded from environment variables at startup and hot-reloadable via
  `PUT /admin/config/rate-limits/{ruleName}`.
- **Counting** — `InMemoryRateLimiterStore` keeps a per-key deque of hit timestamps behind a
  lock (thread-safe sliding window). It implements the `RateLimiterStore` protocol so a
  shared backend (e.g. Redis) can be substituted for multi-instance deployments without
  touching `RateLimiter` or the API layer.
- **Responses** — allowed requests get `X-RateLimit-Limit/Remaining/Reset` headers; denied
  requests get `429` with `Retry-After` and a JSON error body.
- **Observability** — every decision increments `RateLimitMetrics`, exposed via
  `GET /admin/metrics` alongside current rule config, and denials are logged.

## Scaling path

- Read-heavy: front redirects with a cache (code -> long_url).
- Write scale: replace `id_seq` with a sharded/range id allocator.
- Analytics: stream click events to a queue and aggregate out-of-band.
- Rate limiting: swap `InMemoryRateLimiterStore` for a shared store (Redis) to keep limits
  consistent across horizontally-scaled instances.
