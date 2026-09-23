"""Run the URL shortener on the standard-library WSGI server."""

from __future__ import annotations

import os
from wsgiref.simple_server import make_server

from .api import WSGIApp


def main(host: str | None = None, port: int | None = None) -> None:
    # Bind address/port come from the environment so the same entrypoint works on a
    # laptop (127.0.0.1) and inside a container (0.0.0.0) without a code change.
    host = host or os.environ.get("SHORTENER_HOST", "127.0.0.1")
    port = port or int(os.environ.get("SHORTENER_PORT", "8000"))
    app = WSGIApp()
    with make_server(host, port, app) as httpd:
        print(f"URL shortener listening on http://{host}:{port}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
