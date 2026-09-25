"""Entrypoint to run the performance toolkit's HTTP API.

Configuration is environment-driven, consistent with the rest of the repo
(see ``url_shortener/config.py``).
"""

from __future__ import annotations

import os

from .api import build_server
from .service import PerfToolkitService

HOST = os.environ.get("PERF_TOOLKIT_HOST", "127.0.0.1")
PORT = int(os.environ.get("PERF_TOOLKIT_PORT", "8100"))


def main(host: str | None = None, port: int | None = None) -> None:
    host = host or HOST
    port = PORT if port is None else port
    server = build_server(host, port, PerfToolkitService())
    print(f"Perf toolkit listening on http://{host}:{port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
