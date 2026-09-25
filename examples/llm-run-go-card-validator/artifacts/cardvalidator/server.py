"""HTTP transport: stdlib http.server wired to the Application dispatcher.

Runnable via `python -m cardvalidator.server`. Host/port/storage are
configurable via CV_HOST / CV_PORT / CV_DB_PATH env vars or --host/--port.
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .app import Application
from .config import Config


def make_handler(app: Application):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # the Application already logs structured audit events

        def _read_body(self) -> str:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length:
                return self.rfile.read(length).decode("utf-8")
            return ""

        def _respond(self, status: int, payload, content_type: str):
            if content_type == "application/json":
                body = json.dumps(payload).encode("utf-8")
            else:
                body = payload.encode("utf-8") if isinstance(payload, str) else payload
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            status, payload, content_type = app.handle("GET", self.path, self.headers, "")
            self._respond(status, payload, content_type)

        def do_POST(self):
            body = self._read_body()
            status, payload, content_type = app.handle("POST", self.path, self.headers, body)
            self._respond(status, payload, content_type)

    return Handler


def run(host: str = None, port: int = None, config: Config = None):
    """Build and return (httpd, app) without starting serve_forever()."""
    config = config or Config()
    host = host if host is not None else config.host
    port = port if port is not None else config.port
    app = Application(config=config)
    handler = make_handler(app)
    httpd = ThreadingHTTPServer((host, port), handler)
    return httpd, app


def main():
    parser = argparse.ArgumentParser(description="Card transaction validation microservice")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    config = Config()
    httpd, app = run(args.host, args.port, config)
    print(f"cardvalidator listening on {httpd.server_address}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()
        app.close()


if __name__ == "__main__":
    main()
