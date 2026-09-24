"""Entrypoint for running the URL shortener HTTP service."""
import sys

from urlshortener.db import Database
from urlshortener.server import build_server


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    db = Database("urlshortener.db")
    server = build_server(db, port=port)
    print(f"Serving on port {server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.stop_sweeper.set()
        server.shutdown()


if __name__ == "__main__":
    main()
