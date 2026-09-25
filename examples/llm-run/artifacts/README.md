# URL Shortener (standard-library runnable slice)

A single-process URL shortener implementing the first runnable slice of a
larger distributed design. It creates unique base62 short codes, redirects
with low latency, tracks click analytics asynchronously, and supports custom
aliases, expiration, lightweight API-key ownership, and bulk creation.

Implemented entirely with the Python standard library:
- **HTTP**: `http.server` (`ThreadingHTTPServer` + `BaseHTTPRequestHandler`)
- **Persistence**: SQLite (`sqlite3`), durable across restarts
- **Async analytics**: an in-memory queue (`collections.deque` + `threading.Lock`)
  drained by a background worker thread, so redirects never block on analytics writes
- **Caching**: an in-memory TTL cache for hot short_code -> long_url lookups
- **Rate limiting**: an in-memory token bucket per API key / IP
- **Expiration & retention**: a scheduler thread deactivates expired URLs and
  purges click events past the retention window

## Run

```
python -m shortener.server
```

Environment variables (all optional):

| Variable              | Default              | Meaning                              |
|-----------------------|----------------------|---------------------------------------|
| `SHORTENER_HOST`      | `0.0.0.0`             | Bind host                             |
| `SHORTENER_PORT`      | `8080`                | Bind port                             |
| `SHORTENER_DB_PATH`   | `shortener.db`        | SQLite file path                      |
| `SHORTENER_BASE_URL`  | `http://<host>:<port>`| Base URL used to build `short_url`    |
| `RATE_LIMIT_CAPACITY` | `50`                  | Token bucket capacity per client      |
| `RATE_LIMIT_REFILL`   | `20`                  | Tokens refilled per second per client |

## API examples (curl)

Create a short URL:
```
curl -X POST http://localhost:8080/api/urls \
  -H "Content-Type: application/json" \
  -d '{"long_url": "https://example.com/some/very/long/path"}'
```

Bulk create:
```
curl -X POST http://localhost:8080/api/urls/bulk \
  -H "Content-Type: application/json" \
  -d '{"urls": [{"long_url": "https://example.com/1"}, {"long_url": "https://example.com/2", "custom_alias": "two"}]}'
```

Redirect (follow with `-L`, or omit `-L` to just see the 302 + Location header):
```
curl -i http://localhost:8080/abc123
```

Get stats:
```
curl http://localhost:8080/api/urls/abc123/stats
```

Update a short URL (optionally send `X-API-Key` if the URL has an owner):
```
curl -X PUT http://localhost:8080/api/urls/abc123 \
  -H "Content-Type: application/json" \
  -d '{"long_url": "https://example.com/new-destination"}'
```

Delete (deactivate) a short URL:
```
curl -X DELETE http://localhost:8080/api/urls/abc123
```

Create an API key:
```
curl -X POST http://localhost:8080/api/keys \
  -H "Content-Type: application/json" \
  -d '{"owner_name": "alice"}'
```

## API documentation

See `openapi.yaml` for the full OpenAPI 3.0 specification of all endpoints,
request/response bodies, and error status codes (400, 403, 404, 409, 429).

## Tests

Unit tests cover validation, base62 encoding, and the service layer
(create/resolve/update/delete/stats/bulk). Integration tests spin up the real
HTTP server on an ephemeral localhost port (in-process, in a background
thread) and drive every endpoint end to end, including error cases.

Run all tests:
```
python -m unittest discover -s tests -p "test_*.py" -v
```
