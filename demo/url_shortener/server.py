"""Run the URL shortener on the standard-library WSGI server.

Configuration comes from the environment (see ``config.py``) so the same entrypoint
works on a laptop and in a container: ``SHORTENER_STORE=sqlite`` with
``SHORTENER_DB_PATH`` selects the durable backend; the default is in-memory.
Rate limiting is enabled by default (see ``ratelimit.py`` and the
``SHORTENER_RATE_LIMIT_*`` environment variables in ``config.py``).
"""

from __future__ import annotations

from wsgiref.simple_server import make_server

from . import config
from .api import WSGIApp
from .ratelimit import build_rate_limiter
from .service import ShortenerService
from .store import InMemoryStore, SqliteStore


def build_app() -> WSGIApp:
    store = SqliteStore(config.DB_PATH) if config.STORE_BACKEND == "sqlite" else InMemoryStore()
    service = ShortenerService(store=store, base_url=config.BASE_URL)
    return WSGIApp(service=service, limiter=build_rate_limiter())


def main(host: str | None = None, port: int | None = None) -> None:
    host = host or config.HOST
    port = port or config.PORT
    with make_server(host, port, build_app()) as httpd:
        print(f"URL shortener listening on http://{host}:{port} (store={config.STORE_BACKEND})")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
