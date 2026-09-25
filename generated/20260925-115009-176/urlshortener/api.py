"""HTTP API layer: stdlib http.server handler implementing the REST
endpoints. Thin by design -- all business logic lives in `service`.
"""

import json
import logging
import re
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

from .validation import ValidationError
from .service import NotFoundError, ConflictError, ForbiddenError, RateLimitError

logger = logging.getLogger("urlshortener.api")

CREATE_URL_PATH = re.compile(r"^/api/v1/urls/?$")
CREATE_USER_PATH = re.compile(r"^/api/v1/users/?$")
ANALYTICS_PATH = re.compile(r"^/api/v1/urls/(?P<code>[^/]+)/analytics/?$")
URL_META_PATH = re.compile(r"^/api/v1/urls/(?P<code>[^/]+)/?$")
REDIRECT_PATH = re.compile(r"^/api/v1/(?P<code>[^/]+)/?$")

STATUS_MAP = [
    (ValidationError, 400),
    (ConflictError, 409),
    (ForbiddenError, 403),
    (NotFoundError, 404),
    (RateLimitError, 429),
]


def _status_for(exc):
    for cls, status in STATUS_MAP:
        if isinstance(exc, cls):
            return status
    return 500


def build_handler(service, base_url):
    """Return a BaseHTTPRequestHandler subclass bound to `service`."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "URLShortener/1.0"

        def log_message(self, fmt, *args):
            logger.info("%s - %s", self.client_address[0], fmt % args)

        # -- helpers ----------------------------------------------------
        def _send_json(self, status, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error(self, exc):
            status = _status_for(exc)
            if status == 500:
                logger.exception("unhandled error")
            self._send_json(status, {"error": str(exc)})

        def _read_json(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                raise ValidationError("request body must be valid JSON")

        def _bearer_token(self):
            auth = self.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                return auth[7:].strip()
            return None

        def _client_ip(self):
            return self.client_address[0]

        # -- routing ------------------------------------------------------
        def do_POST(self):
            path = urlparse(self.path).path
            try:
                if CREATE_URL_PATH.match(path):
                    return self._create_url()
                if CREATE_USER_PATH.match(path):
                    return self._create_user()
                self._send_json(404, {"error": "not found"})
            except Exception as e:
                self._send_error(e)

        def do_GET(self):
            path = urlparse(self.path).path
            try:
                m = ANALYTICS_PATH.match(path)
                if m:
                    return self._get_analytics(m.group("code"))
                m = URL_META_PATH.match(path)
                if m:
                    return self._get_metadata(m.group("code"))
                m = REDIRECT_PATH.match(path)
                if m:
                    return self._redirect(m.group("code"))
                self._send_json(404, {"error": "not found"})
            except Exception as e:
                self._send_error(e)

        def do_DELETE(self):
            path = urlparse(self.path).path
            try:
                m = URL_META_PATH.match(path)
                if m:
                    return self._delete(m.group("code"))
                self._send_json(404, {"error": "not found"})
            except Exception as e:
                self._send_error(e)

        # -- handlers -----------------------------------------------------
        def _create_url(self):
            body = self._read_json()
            long_url = body.get("long_url")
            custom_alias = body.get("custom_alias")
            expires_at = body.get("expires_at")
            owner_id = service.authenticate(self._bearer_token())
            result = service.create_short_url(
                long_url, custom_alias, expires_at, owner_id, self._client_ip()
            )
            result["short_url"] = "{}/api/v1/{}".format(
                base_url.rstrip("/"), result["short_code"]
            )
            self._send_json(201, result)

        def _create_user(self):
            self._send_json(201, service.create_user())

        def _redirect(self, code):
            row = service.get_url_for_redirect(code, self._client_ip())
            referrer = self.headers.get("Referer")
            user_agent = self.headers.get("User-Agent")
            country = self.headers.get("X-Country", "Unknown")
            service.record_click(code, referrer, user_agent, country)
            self.send_response(302)
            self.send_header("Location", row["long_url"])
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _get_metadata(self, code):
            row = service.get_metadata(code)
            self._send_json(200, {
                "short_code": row["short_code"],
                "long_url": row["long_url"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "click_count": row["click_count"],
                "is_active": bool(row["is_active"]),
                "owner_id": row["owner_id"],
            })

        def _delete(self, code):
            owner_id = service.authenticate(self._bearer_token())
            service.deactivate(code, owner_id)
            self._send_json(200, {"short_code": code, "is_active": False})

        def _get_analytics(self, code):
            owner_id = service.authenticate(self._bearer_token())
            self._send_json(200, service.get_analytics(code, owner_id))

    return Handler

