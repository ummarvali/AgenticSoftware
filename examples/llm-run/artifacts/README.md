# Generated Service

A high-throughput URL shortening service that generates unique short codes for long URLs, persists mappings durably, redirects users at scale, and asynchronously captures click analytics (timestamp, referrer, geo, device) without blocking the redirect path. The system is split into a stateless API/redirect tier, a persistent key-value store for mappings, a distributed ID/code generator, an async analytics pipeline backed by a message queue and time-series/OLAP store, and a caching layer to absorb read-heavy traffic.

## Tests

`python -m unittest discover -s tests`
