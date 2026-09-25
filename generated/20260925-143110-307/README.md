# URL Shortener (prototype)

A single-process URL shortener built entirely on the Python standard
library. It exposes a REST API to create short links (with optional custom
alias and TTL), redirect visitors to the original URL with low latency, and
report basic click analytics. Data is persisted durably in SQLite; hot
redirect lookups are served from an in-process LRU cache. A background
thread drains a queue of click events into SQLite so redirect latency is not
coupled to analytics write cost, and another background thread periodically
purges expired links.

This is designed to later evolve into Postgres + Redis + horizontally
replicated API nodes, but runs standalone with zero third-party
dependencies.

## Running

```
python -m urlshortener.server
```

Configuration is via environment variables (all optional):

| Variable | Default | Meaning |
|---|---|---|
| `URLSHORTENER_HOST` | `127.0.0.1` | Listen address |
| `URLSHORTENER_PORT` | `8080` | Listen port |
| `URLSHORTENER_DB_PATH` | `urlshortener.db` | SQLite file path |
| `URLSHORTENER_BASE_URL` | `http://<host>:<port>` | Prefix used to build `short_url` |
| `URLSHORTENER_HTTPS_ONLY` | `false` | Reject non-https long URLs when `true` |
| `URLSHORTENER_MAX_URL_LENGTH` | `2048` | Max accepted long URL length |
| `URLSHORTENER_DEFAULT_RATE_LIMIT` | `60` | Requests/min granted to a new API key |
| `URLSHORTENER_ANON_RATE_LIMIT` | `20` | Requests/min for unauthenticated create calls, keyed by peer IP |
| `URLSHORTENER_KEY_CREATE_RATE_LIMIT` | `5` | Requests/min for the key-creation endpoint, keyed by peer IP |
| `URLSHORTENER_CACHE_CAPACITY` | `10000` | Max entries in the redirect LRU cache |
| `URLSHORTENER_SWEEP_INTERVAL` | `60` | Seconds between expiration sweeps |
| `URLSHORTENER_SHORT_CODE_LENGTH` | `7` | Length of generated short codes |
| `URLSHORTENER_DENYLIST_HOSTS` | `localhost,127.0.0.1,0.0.0.0,::1` | Hostnames blocked as redirect targets |

## API examples (curl)

Create a short URL:
```
curl -X POST http://127.0.0.1:8080/api/v1/urls \
  -H 'Content-Type: application/json' \
  -d '{"long_url": "https://example.com/some/very/long/path"}'
```

Create with a custom alias and a 1-hour TTL:
```
curl -X POST http://127.0.0.1:8080/api/v1/urls \
  -H 'Content-Type: application/json' \
  -d '{"long_url": "https://example.com/x", "custom_alias": "my-link", "ttl_seconds": 3600}'
```

Redirect (follow with `-L`, or inspect the 302 with `-i`):
```
curl -i http://127.0.0.1:8080/abc1234
```

Get URL metadata:
```
curl http://127.0.0.1:8080/api/v1/urls/abc1234
```

Get analytics:
```
curl http://127.0.0.1:8080/api/v1/urls/abc1234/analytics
```

Create an API key (self-service, rate-limited by your IP; does not affect
anyone else's limits):
```
curl -X POST http://127.0.0.1:8080/api/v1/keys
```

Health check:
```
curl http://127.0.0.1:8080/healthz
```

Metrics:
```
curl http://127.0.0.1:8080/metrics
```

OpenAPI document:
```
curl http://127.0.0.1:8080/openapi.json
```

## Notes on security

* Create/redirect targets are validated: only `http`/`https` schemes are
  accepted, length is bounded, and loopback/private/reserved hosts are
  blocked to prevent open-redirect and internal-network abuse.
* An `X-API-Key` header is only treated as an identity if it matches an
  active key stored in the database; otherwise rate limiting falls back to
  the verified peer IP address. No endpoint changes another caller's rate
  limit or any other configuration; all such settings are environment
  variables set at process start.
* Error responses are generic (`{"error": "..."}`) and never leak internal
  details or stack traces.

## Tests

```
python -m unittest discover -s tests -v
```

Tests include unit tests for validation, short-code generation, the rate
limiter and the core service logic (dedupe, custom alias conflicts,
expiration), plus in-process HTTP integration tests covering every endpoint
and the main error cases (invalid input -> 400, unknown resource -> 404,
expired -> 410, rate limit -> 429).
