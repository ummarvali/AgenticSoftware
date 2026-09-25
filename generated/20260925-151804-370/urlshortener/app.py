"""WSGI application: routing and request handlers for the URL shortener."""
import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from . import security, validator, analytics
from .db import LRUCache, utcnow_iso

logger = logging.getLogger("urlshortener")

CODE_RE = r"[A-Za-z0-9_-]{1,32}"


class AliasConflict(Exception):
    pass


class Application:
    def __init__(self, db, config):
        self.db = db
        self.config = config
        self.cache = LRUCache(config.cache_size)
        self.create_limiter = security.RateLimiter(config.rate_limit_per_minute)
        self.redirect_limiter = security.RateLimiter(config.redirect_rate_limit_per_minute)
        self._routes = [
            (re.compile(r"^/healthz$"), "GET", self.handle_health),
            (re.compile(r"^/api/v1/urls$"), "POST", self.handle_create),
            (re.compile(r"^/api/v1/urls/(?P<code>%s)/analytics$" % CODE_RE), "GET", self.handle_analytics),
            (re.compile(r"^/api/v1/urls/(?P<code>%s)$" % CODE_RE), "GET", self.handle_get_meta),
            (re.compile(r"^/api/v1/urls/(?P<code>%s)$" % CODE_RE), "DELETE", self.handle_delete),
            (re.compile(r"^/(?P<code>%s)$" % CODE_RE), "GET", self.handle_redirect),
        ]

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")
        try:
            for pattern, m, handler in self._routes:
                match = pattern.match(path)
                if match and m == method:
                    status, headers, body = handler(environ, **match.groupdict())
                    start_response(status, headers)
                    return [body]
            for pattern, _m, _handler in self._routes:
                if pattern.match(path):
                    return self._error(start_response, "405 Method Not Allowed", "method not allowed")
            return self._error(start_response, "404 Not Found", "resource not found")
        except security.AuthError:
            return self._error(start_response, "401 Unauthorized", "authentication required")
        except security.RateLimitError:
            return self._error(start_response, "429 Too Many Requests", "rate limit exceeded")
        except AliasConflict:
            return self._error(start_response, "409 Conflict", "alias already in use")
        except PermissionError:
            return self._error(start_response, "403 Forbidden", "not permitted")
        except LookupError:
            return self._error(start_response, "404 Not Found", "resource not found")
        except ValueError as exc:
            return self._error(start_response, "400 Bad Request", str(exc))
        except Exception:
            logger.exception("unhandled error handling %s %s", method, path)
            return self._error(start_response, "500 Internal Server Error", "internal server error")

    @staticmethod
    def _error(start_response, status, message):
        body = json.dumps({"error": message}).encode("utf-8")
        start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
        return [body]

    @staticmethod
    def _json_response(status, obj, extra_headers=None):
        body = json.dumps(obj).encode("utf-8")
        headers = [("Content-Type", "application/json"), ("Content-Length", str(len(body)))]
        if extra_headers:
            headers.extend(extra_headers)
        return status, headers, body

    # -- handlers ----------------------------------------------------
    def handle_health(self, environ):
        ok = self.db.health_check()
        return self._json_response("200 OK", {"status": "ok", "db": "ok" if ok else "error"})

    def handle_create(self, environ):
        key, _owner = security.authenticate(self.db, environ)
        self.create_limiter.check(key)
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 65536:
            raise ValueError("request body required")
        raw = environ["wsgi.input"].read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            raise ValueError("malformed JSON body")
        if not isinstance(payload, dict):
            raise ValueError("malformed JSON body")

        long_url = payload.get("long_url")
        validator.validate_url(long_url, self.config.max_url_length)

        custom_alias = payload.get("custom_alias")
        if custom_alias:
            validator.validate_alias(custom_alias)

        ttl = payload.get("ttl_seconds")
        expires_at = None
        if ttl is not None:
            try:
                ttl_int = int(ttl)
                if ttl_int <= 0:
                    raise ValueError()
            except Exception:
                raise ValueError("ttl_seconds must be a positive integer")
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_int)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

        created_at = utcnow_iso()
        if custom_alias:
            short_code = custom_alias
            try:
                self.db.create_mapping(short_code, long_url, key, created_at, expires_at, True)
            except sqlite3.IntegrityError:
                raise AliasConflict()
        else:
            short_code = None
            for _ in range(5):
                candidate = self.db.next_short_code()
                try:
                    self.db.create_mapping(candidate, long_url, key, created_at, expires_at, False)
                    short_code = candidate
                    break
                except sqlite3.IntegrityError:
                    continue
            if short_code is None:
                raise RuntimeError("could not allocate short code")

        self.cache.invalidate(short_code)
        short_url = "%s/%s" % (self.config.base_url.rstrip("/"), short_code)
        return self._json_response(
            "201 Created",
            {
                "short_code": short_code,
                "short_url": short_url,
                "long_url": long_url,
                "created_at": created_at,
                "expires_at": expires_at,
            },
        )

    def handle_get_meta(self, environ, code):
        security.authenticate(self.db, environ)
        mapping = self.db.get_mapping(code)
        if not mapping:
            raise LookupError()
        owner_name = self.db.verify_api_key(mapping["owner_api_key"]) or "unknown"
        return self._json_response(
            "200 OK",
            {
                "short_code": mapping["short_code"],
                "long_url": mapping["long_url"],
                "owner": owner_name,
                "created_at": mapping["created_at"],
                "expires_at": mapping["expires_at"],
                "is_active": bool(mapping["is_active"]),
            },
        )

    def handle_delete(self, environ, code):
        key, _owner = security.authenticate(self.db, environ)
        mapping = self.db.get_mapping(code)
        if not mapping:
            raise LookupError()
        if mapping["owner_api_key"] != key:
            raise PermissionError()
        self.db.deactivate_mapping(code)
        self.cache.invalidate(code)
        return self._json_response("200 OK", {"short_code": code, "is_active": False})

    def handle_analytics(self, environ, code):
        security.authenticate(self.db, environ)
        mapping = self.db.get_mapping(code)
        if not mapping:
            raise LookupError()
        return self._json_response("200 OK", analytics.get_analytics(self.db, code))

    def handle_redirect(self, environ, code):
        peer = environ.get("REMOTE_ADDR", "unknown")
        self.redirect_limiter.check(peer)
        mapping = self.cache.get(code)
        if mapping is None:
            mapping = self.db.get_mapping(code)
            if mapping:
                self.cache.set(code, mapping)
        if not mapping or not mapping["is_active"]:
            raise LookupError()
        if mapping["expires_at"] and mapping["expires_at"] < utcnow_iso():
            self.db.deactivate_mapping(code)
            self.cache.invalidate(code)
            raise LookupError()
        referrer = environ.get("HTTP_REFERER", "")
        user_agent = environ.get("HTTP_USER_AGENT", "")
        analytics.record_click(self.db, code, referrer, user_agent)
        headers = [("Location", mapping["long_url"]), ("Content-Length", "0")]
        return "302 Found", headers, b""
