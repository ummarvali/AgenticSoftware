"""HTTP API for the performance toolkit, built only on ``http.server``.

Routes:
    POST /runs
    GET  /runs/{run_id}
    GET  /runs/{run_id}/bottlenecks
    GET  /optimizations
    POST /optimizations
    POST /optimizations/{id}/rollback
    POST /regression-tests
    GET  /comparisons
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .service import NotFoundError, PerfToolkitService

_RUN_DETAIL_RE = re.compile(r"^/runs/(\d+)$")
_RUN_BOTTLENECKS_RE = re.compile(r"^/runs/(\d+)/bottlenecks$")
_OPT_ROLLBACK_RE = re.compile(r"^/optimizations/([^/]+)/rollback$")

_REASON = {
    200: "OK",
    201: "Created",
    400: "Bad Request",
    404: "Not Found",
    500: "Internal Server Error",
}


def make_handler(service: PerfToolkitService):
    """Build a request handler class bound to ``service`` (closure, not globals)."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args) -> None:  # silence default stderr logging
            pass

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            path = urlparse(self.path).path
            try:
                if path == "/optimizations":
                    return self._json(200, service.list_optimizations())
                if path == "/comparisons":
                    return self._json(200, service.list_comparisons())
                match = _RUN_BOTTLENECKS_RE.match(path)
                if match:
                    return self._json(200, service.get_bottlenecks(int(match.group(1))))
                match = _RUN_DETAIL_RE.match(path)
                if match:
                    return self._json(200, service.get_run(int(match.group(1))))
                return self._json(404, {"error": "not found"})
            except NotFoundError:
                return self._json(404, {"error": "not found"})
            except Exception:  # defensive: never leak internals
                return self._json(500, {"error": "internal error"})

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            path = urlparse(self.path).path
            try:
                body = self._read_body()
                if path == "/runs":
                    iterations = int(body.get("iterations", 50))
                    return self._json(201, service.create_run(iterations=iterations))
                if path == "/optimizations":
                    opt_id = body.get("id")
                    if not opt_id:
                        return self._json(400, {"error": "missing optimization id"})
                    return self._json(200, service.apply_optimization(opt_id))
                if path == "/regression-tests":
                    return self._json(200, service.run_regression_tests())
                match = _OPT_ROLLBACK_RE.match(path)
                if match:
                    return self._json(200, service.rollback_optimization(match.group(1)))
                return self._json(404, {"error": "not found"})
            except NotFoundError:
                return self._json(404, {"error": "not found"})
            except (ValueError, TypeError):
                return self._json(400, {"error": "invalid request"})
            except Exception:  # defensive: never leak internals
                return self._json(500, {"error": "internal error"})

        def _read_body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                length = 0
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            if not raw:
                return {}
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return {}
            return data if isinstance(data, dict) else {}

        def _json(self, status: int, payload) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status, _REASON.get(status))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def build_server(host: str = "127.0.0.1", port: int = 0, service: PerfToolkitService | None = None):
    """Build (but do not start) a threading HTTP server for the toolkit API."""
    service = service or PerfToolkitService()
    handler = make_handler(service)
    return ThreadingHTTPServer((host, port), handler)
