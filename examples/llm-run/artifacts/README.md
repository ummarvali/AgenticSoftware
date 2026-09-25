# Generated Service

> Synthesized by the Repair agent from the design (the model's code bundle did not include a README).

A URL shortening service exposing REST endpoints to create short aliases, redirect to original URLs, and retrieve analytics. The prototype runs as a single Python process using only the standard library (http.server) with SQLite for durable storage of mappings, click events, and API keys. Analytics events are recorded synchronously on redirect but processed/aggregated asynchronously via a background thread to simulate near-real-time batching. Caching of hot short codes is done in-memory (LRU dict) to keep redirect latency low. The design is intended to evolve into a horizontally scalable, cloud-agnostic system using PostgreSQL, Redis, and a message queue in production.

## Endpoints served by this slice

- `POST /api/urls` — Create a shortened URL, optional custom alias, expiration, and API key
- `GET /{short_code}` — Redirect to the original long URL and asynchronously record a click event
- `GET /api/urls/{short_code}/analytics` — Retrieve click analytics summary and recent events for a short URL
- `GET /api/urls/{short_code}` — Retrieve metadata for a short URL (owner, expiration, creation time) without redirecting
- `DELETE /api/urls/{short_code}` — Delete/deactivate a short URL (requires matching API key if one was set at creation)

Full contract: `openapi.yaml`.

## Run

`python -m urlshortener.server` — see that module for the default host, port and storage path.

## Tests

`python -m unittest discover -s tests`

The engineering record for this run is `ENGINEERING_SUMMARY.md`.
