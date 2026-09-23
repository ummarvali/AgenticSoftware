"""WSGI HTTP API for the URL shortener: create/read/update/delete + redirect."""
import json
import re
from urllib.parse import urlparse
from wsgiref.simple_server import make_server

from .service import URLService

service = URLService()

_SHORT_URL_RE = re.compile(r"^/api/v1/urls/([^/]+)$")
_ANALYTICS_RE = re.compile(r"^/api/v1/urls/([^/]+)/analytics$")


def _json_response(start_response, status, payload):
    body = json.dumps(payload).encode("utf-8")
    headers = [("Content-Type", "application/json"), ("Content-Length", str(len(body)))]
    start_response(status, headers)
    return [body]


def _read_json(environ):
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length <= 0:
        return {}
    raw = environ["wsgi.input"].read(length)
    return json.loads(raw or b"{}")


def _mapping_dict(m):
    return {
        "short_code": m.short_code, "long_url": m.long_url, "created_at": m.created_at,
        "updated_at": m.updated_at, "expires_at": m.expires_at, "is_active": m.is_active,
        "click_count": m.click_count_cache,
    }


def app(environ, start_response):
    method = environ["REQUEST_METHOD"]
    path = urlparse(environ.get("PATH_INFO", "")).path

    if method == "GET" and path == "/api/v1/health":
        return _json_response(start_response, "200 OK", service.health())

    if method == "POST" and path == "/api/v1/urls":
        data = _read_json(environ)
        long_url = data.get("long_url")
        if not long_url:
            return _json_response(start_response, "400 Bad Request", {"error": "long_url required"})
        try:
            mapping = service.create_url(
                long_url, custom_alias=data.get("custom_alias"), expires_at=data.get("expires_at")
            )
        except ValueError as e:
            return _json_response(start_response, "409 Conflict", {"error": str(e)})
        resp = {
            "short_code": mapping.short_code,
            "short_url": f"http://localhost/{mapping.short_code}",
            "long_url": mapping.long_url, "created_at": mapping.created_at,
            "expires_at": mapping.expires_at,
        }
        return _json_response(start_response, "201 Created", resp)

    m = _ANALYTICS_RE.match(path)
    if method == "GET" and m:
        code = m.group(1)
        result = service.get_analytics(code)
        if result is None:
            return _json_response(start_response, "404 Not Found", {"error": "not found"})
        return _json_response(start_response, "200 OK", result)

    m = _SHORT_URL_RE.match(path)
    if m:
        code = m.group(1)
        if method == "GET":
            mapping = service.get_url(code)
            if not mapping:
                return _json_response(start_response, "404 Not Found", {"error": "not found"})
            return _json_response(start_response, "200 OK", _mapping_dict(mapping))
        if method == "PUT":
            data = _read_json(environ)
            mapping = service.update_url(
                code, long_url=data.get("long_url"), is_active=data.get("is_active")
            )
            if not mapping:
                return _json_response(start_response, "404 Not Found", {"error": "not found"})
            return _json_response(start_response, "200 OK", {
                "short_code": mapping.short_code, "long_url": mapping.long_url,
                "updated_at": mapping.updated_at, "is_active": mapping.is_active,
            })
        if method == "DELETE":
            ok = service.delete_url(code)
            if not ok:
                return _json_response(start_response, "404 Not Found", {"error": "not found"})
            return _json_response(start_response, "200 OK", {"short_code": code, "deleted": True})

    if method == "GET" and path.startswith("/") and path.count("/") == 1 and not path.startswith("/api"):
        code = path[1:]
        long_url = service.redirect(
            code, ip=environ.get("REMOTE_ADDR", "0.0.0.0"),
            user_agent=environ.get("HTTP_USER_AGENT", ""),
            referrer=environ.get("HTTP_REFERER", ""),
        )
        if not long_url:
            return _json_response(start_response, "404 Not Found", {"error": "not found"})
        start_response("302 Found", [("Location", long_url), ("Content-Length", "0")])
        return [b""]

    return _json_response(start_response, "404 Not Found", {"error": "not found"})


def main():
    with make_server("", 8000, app) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
