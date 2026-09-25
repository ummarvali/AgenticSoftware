"""URL Shortener package.

A single-process, standard-library-only implementation of a scalable URL
shortener API. SQLite provides durable persistence, an in-memory LRU cache
stands in for a distributed cache (e.g. Redis), and synchronous event
writes stand in for an async analytics pipeline (e.g. Kafka). The HTTP API
contract is designed to remain stable if those pieces are later swapped
for distributed infrastructure.
"""

__version__ = "0.1.0"

