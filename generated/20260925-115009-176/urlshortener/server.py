"""Runnable entry point: `python -m urlshortener.server`.

Configuration via environment variables:
  HOST      - bind address (default 0.0.0.0)
  PORT      - bind port (default 8000)
  DB_PATH   - SQLite file path (default urlshortener.db)
  BASE_URL  - public base URL used to build short_url values in responses
"""

import os
import logging
from http.server import ThreadingHTTPServer

from .storage import Storage
from .service import UrlShortenerService
from .api import build_handler


def create_server(host=None, port=None, db_path=None, base_url=None):
    host = host or os.environ.get("HOST", "0.0.0.0")
    port = int(port or os.environ.get("PORT", "8000"))
    db_path = db_path or os.environ.get("DB_PATH", "urlshortener.db")
    base_url = base_url or os.environ.get("BASE_URL", "http://{}:{}".format(host, port))

    storage = Storage(db_path)
    service = UrlShortenerService(storage)
    handler_cls = build_handler(service, base_url)
    httpd = ThreadingHTTPServer((host, port), handler_cls)
    return httpd, service


def main():
    logging.basicConfig(level=logging.INFO,
                         format="%(asctime)s %(levelname)s %(name)s %(message)s")
    httpd, _ = create_server()
    host, port = httpd.server_address[:2]
    print("URL shortener listening on {}:{}".format(host, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()

