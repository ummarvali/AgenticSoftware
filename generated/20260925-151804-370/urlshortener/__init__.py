"""URL shortening service package.

Modules:
    config      - environment-driven configuration
    db          - SQLite persistence layer, short-code generation, LRU cache
    validator   - input URL / alias validation
    security    - API key authentication and rate limiting
    analytics   - click analytics recording/aggregation
    app         - WSGI application (routing + handlers)
    server      - runnable entry point
"""

__version__ = "1.0.0"
