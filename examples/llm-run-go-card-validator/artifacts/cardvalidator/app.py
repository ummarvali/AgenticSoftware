"""WSGI HTTP API layer exposing validate/health/readiness endpoints."""
import json
from wsgiref.simple_server import make_server

from .config import Config
from .storage import Storage
from . import rules


def create_app(config=None, storage=None):
    config = config or Config.load_from_env()
    storage = storage or Storage(config.db_path)

    def app(environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "")
        try:
            if method == "GET" and path == "/healthz":
                body = json.dumps({"status": "ok"}).encode("utf-8")
                start_response("200 OK", [("Content-Type", "application/json")])
                return [body]

            if method == "GET" and path == "/readyz":
                ok = storage.check_writable()
                body = json.dumps({
                    "status": "ready" if ok else "not_ready",
                    "checks": {"sqlite": ok},
                }).encode("utf-8")
                status_line = "200 OK" if ok else "503 Service Unavailable"
                start_response(status_line, [("Content-Type", "application/json")])
                return [body]

            if method == "POST" and path == "/v1/transactions/validate":
                try:
                    length = int(environ.get("CONTENT_LENGTH") or 0)
                except ValueError:
                    length = 0
                raw = environ["wsgi.input"].read(length) if length else b""
                try:
                    payload = json.loads(raw.decode("utf-8")) if raw else {}
                except Exception:
                    payload = None
                if not isinstance(payload, dict):
                    start_response("400 Bad Request", [("Content-Type", "application/json")])
                    return [json.dumps({"error": "invalid_json"}).encode("utf-8")]
                response, status_code = rules.process_transaction(payload, config, storage)
                status_line = "200 OK" if status_code == 200 else "400 Bad Request"
                start_response(status_line, [("Content-Type", "application/json")])
                return [json.dumps(response).encode("utf-8")]

            start_response("404 Not Found", [("Content-Type", "application/json")])
            return [json.dumps({"error": "not_found"}).encode("utf-8")]
        except Exception:
            start_response("500 Internal Server Error", [("Content-Type", "application/json")])
            return [json.dumps({"error": "internal_error"}).encode("utf-8")]

    app.config = config
    app.storage = storage
    return app


def run_server(host="0.0.0.0", port=8080):
    application = create_app()
    httpd = make_server(host, port, application)
    httpd.serve_forever()


if __name__ == "__main__":
    run_server()
