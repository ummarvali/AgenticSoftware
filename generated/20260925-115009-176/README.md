# URL Shortener (stdlib prototype)

A single-process URL shortener implementing the same API contract as the
target scalable design (DynamoDB/Cassandra + Redis + Kafka), but built
entirely on the Python standard library for a runnable first slice:

- **API Server**: `http.server`-based REST API (create / redirect / metadata / delete / analytics / users)
- **Persistence**: SQLite (`urls`, `users`, `click_events`, `blacklist` tables)
- **Cache**: in-memory LRU cache in front of SQLite reads (stands in for Redis)
- **Analytics**: click events written synchronously on redirect, aggregated on demand via GROUP BY-style logic (stands in for Kafka + batch aggregation)
- **Validation & rate limiting**: URL format checks, blacklist, custom alias validation, per-IP token-bucket rate limiting
- **Auth**: optional bearer-token identification; anonymous URLs are manageable by anyone, owned URLs are restricted to their owner

## Run it

```bash
python -m urlshortener.server
```

Environment variables (all optional):

| Variable  | Default              | Description                     |
|-----------|----------------------|----------------------------------|
| HOST      | 0.0.0.0              | Bind address                     |
| PORT      | 8000                 | Bind port                        |
| DB_PATH   | urlshortener.db      | SQLite file path                 |
| BASE_URL  | http://HOST:PORT     | Base URL used in `short_url`     |

## API examples (curl)

### Create a short URL
```bash
curl -X POST http://localhost:8000/api/v1/urls \
  -H "Content-Type: application/json" \
  -d '{"long_url": "https://example.com/some/long/path"}'
```

### Create a short URL with a custom alias and expiration
```bash
curl -X POST http://localhost:8000/api/v1/urls \
  -H "Content-Type: application/json" \
  -d '{"long_url": "https://example.com", "custom_alias": "my-link", "expires_at": "2999-01-01T00:00:00Z"}'
```

### Redirect
```bash
curl -i http://localhost:8000/api/v1/my-link
```

### Get metadata
```bash
curl http://localhost:8000/api/v1/urls/my-link
```

### Delete (deactivate)
```bash
curl -X DELETE http://localhost:8000/api/v1/urls/my-link
```

### Analytics
```bash
curl http://localhost:8000/api/v1/urls/my-link/analytics
```

### Create a user (obtain API token)
```bash
curl -X POST http://localhost:8000/api/v1/users
```

Authenticated requests attach the token from the user-creation response:
```bash
curl -X POST http://localhost:8000/api/v1/urls \
  -H "Authorization: Bearer <api_token>" \
  -H "Content-Type: application/json" \
  -d '{"long_url": "https://example.com"}'
```

## Tests

```bash
python -m unittest discover -s tests -v
```

Tests include unit tests for the service layer (short code generation,
validation, expiration, rate limiting, analytics aggregation) and
integration tests that start the HTTP server on an ephemeral localhost
port and exercise every endpoint end-to-end, including 400/404 error
cases.

## Notes on scaling to production

This prototype keeps the API contract stable while substituting simpler
components for the ones described in the target architecture:

- SQLite -> distributed KV store (DynamoDB/Cassandra) with sharding by short_code hash
- In-memory LRU cache -> Redis cluster, fronting redirects for sub-100ms latency
- Synchronous click recording -> async ingestion via Kafka + stream aggregation
- In-memory rate limiter -> distributed limiter (e.g. Redis token buckets) shared across nodes
- Single process -> horizontally scaled stateless API nodes behind a load balancer / CDN

