# URL Shortener Service

A single-process URL shortening service built entirely on the Python
standard library. It exposes REST APIs to create, resolve, inspect and
delete shortened URLs, persists mappings and click analytics in SQLite,
and serves redirects through an in-memory LRU cache for low latency.

## Features
- Create short URLs with optional custom alias and optional TTL
- Public, high-throughput redirect endpoint (302)
- Metadata and click-analytics retrieval per short code
- Deactivation ("delete") of short URLs, owner-checked
- API-key authentication for management endpoints (redirect is public)
- Per-identity rate limiting (API key for management calls, peer IP for redirects)
- URL validation to reject malformed/malicious targets (non-http(s) schemes,
  private/loopback IPs, `javascript:`/`data:` links, etc.)

## Running

The service reads configuration from environment variables (no endpoint
can change these, by design):

| Variable | Default | Description |
|---|---|---|
| `HOST` | `127.0.0.1` | Bind address |
| `PORT` | `8000` | Bind port |
| `DB_PATH` | `urlshortener.db` | SQLite file path |
| `BASE_URL` | `http://HOST:PORT` | Base used to build `short_url` |
| `SHORTENER_API_KEYS` | *(empty)* | Seed API keys: `key1:owner1,key2:owner2` |
| `RATE_LIMIT_PER_MINUTE` | `120` | Per-API-key limit for management calls |
| `REDIRECT_RATE_LIMIT_PER_MINUTE` | `1200` | Per-IP limit for redirects |
| `CACHE_SIZE` | `1000` | Redirect LRU cache entries |
| `MAX_URL_LENGTH` | `2048` | Max accepted `long_url` length |

Start the server (creating an API key first):

```bash
export SHORTENER_API_KEYS="devkey:alice"
python -m urlshortener.server --host 127.0.0.1 --port 8000
```

## API examples (curl)

Create a short URL:
```bash
curl -X POST http://127.0.0.1:8000/api/v1/urls \
  -H "X-API-Key: devkey" -H "Content-Type: application/json" \
  -d '{"long_url": "https://example.com/some/page", "ttl_seconds": 3600}'
```

Redirect (follows to the long URL, `-i` shows the 302 + Location header):
```bash
curl -i http://127.0.0.1:8000/<short_code>
```

Get metadata:
```bash
curl -H "X-API-Key: devkey" http://127.0.0.1:8000/api/v1/urls/<short_code>
```

Get analytics:
```bash
curl -H "X-API-Key: devkey" http://127.0.0.1:8000/api/v1/urls/<short_code>/analytics
```

Delete (deactivate):
```bash
curl -X DELETE -H "X-API-Key: devkey" http://127.0.0.1:8000/api/v1/urls/<short_code>
```

Health check:
```bash
curl http://127.0.0.1:8000/healthz
```

## Tests

Unit tests cover validation, short-code encoding and rate limiting;
integration tests drive the full HTTP API in-process against an ephemeral
localhost port with a temporary SQLite database.

```bash
python -m unittest discover -s tests -v
```
