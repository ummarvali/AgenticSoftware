"""Run the URL shortener on the standard-library WSGI server."""

from __future__ import annotations

from wsgiref.simple_server import make_server

from .api import WSGIApp


def main(host: str = "127.0.0.1", port: int = 8000) -> None:
    app = WSGIApp()
    with make_server(host, port, app) as httpd:
        print(f"URL shortener listening on http://{host}:{port}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
