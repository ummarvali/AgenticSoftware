# Generated Service

A URL shortening service exposing REST APIs to create, redirect, manage, and analyze short URLs. The prototype is a single Python process using only the standard library (http.server) with SQLite for durable storage of URL mappings, click events, and users/API keys, plus an in-memory LRU cache for hot redirect lookups. The production target architecture uses a NoSQL key-value store for mappings, a dedicated time-series/columnar analytics store, a distributed cache, and async event ingestion for analytics, enabling horizontal scaling and sub-100ms redirects.

## Tests

`python -m unittest discover -s tests`
