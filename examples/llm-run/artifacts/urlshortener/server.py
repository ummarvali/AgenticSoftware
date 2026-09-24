"""HTTP API layer: routing, caching, rate limiting, redirects, analytics."""
import hashlib
import json
import re
import threading
import time
from collections import OrderedDict, defaultdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from . import auth, shortcode, validation

METRICS = defaultdict(int)

OPENAPI = {
    "openapi": "3.0.0",
    "info": {"title": "URL Shortener API", "version": "1.0.0"},
    "paths": {
        "/api/v1/urls": {"post": {"summary": "Create short URL"}},
        "/{short_code}": {"get": {"summary": "Redirect to long URL"}},
        "/api/v1/urls/{short_code}": {
            "get": {"summary": "Get URL metadata"},
            "delete": {"summary": "Deactivate URL"},
        },
        "/api/v1/urls/{short_code}/analytics": {"get": {"summary": "Get analytics"}},
        "/api/v1/auth/register": {"post": {"summary": "Register user"}},
        "/api/v1/auth/login": {"post": {"summary": "Login user"}},
    },
}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class TTLCache:
    """In-memory LRU cache with per-entry TTL for hot short-code lookups."""

    def __init__(self, maxsize=5000, ttl=30):
        self.maxsize = maxsize
        self.ttl = ttl
        self.data = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            item = self.data.get(key)
            if not item:
                return None
            value, exp = item
            if exp < time.time():
                del self.data[key]
                return None
            self.data.move_to_end(key)
            return value

    def set(self, key, value):
        with self.lock:
            self.data[key] = (value, time.time() + self.ttl)
            self.data.move_to_end(key)
            if len(self.data) > self.maxsize:
                self.data.popitem(last=False)

    def invalidate(self, key):
        with self.lock:
            self.data.pop(key, None)


class RateLimiter:
    """Simple in-memory token-bucket rate limiter keyed by IP or API key."""

    def __init__(self, rate=100, per=1.0):
        self.rate = rate
        self.per = per
        self.buckets = {}
        self.lock = threading.Lock()

    def allow(self, key):
        with self.lock:
            now = time.time()
            tokens, last = self.buckets.get(key, (self.rate, now))
            tokens = min(self.rate, tokens + (now - last) * (self.rate / self.per))
            if tokens < 1:
                self.buckets[key] = (tokens, now)
                return False
            self.buckets[key] = (tokens - 1, now)
            return True


def sweep_expired(db, interval=30, stop_event=None):
    """Background sweeper marking expired URLs inactive."""
    stop_event = stop_event or threading.Event()
    while not stop_event.is_set():
        db.execute(
            "UPDATE url_mappings SET is_active=0 WHERE expires_at IS NOT NULL "
            "AND expires_at < ? AND is_active=1",
            (_now(),),
        )
        stop_event.wait(interval)


class Handler(BaseHTTPRequestHandler):
    db = None
    cache = None
    limiter = None
    server_version = "URLShortener/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode())

    def _get_api_key(self):
        hdr = self.headers.get("Authorization", "")
        if hdr.startswith("Bearer "):
            return hdr[7:]
        return self.headers.get("X-Api-Key")

    def _check_rate_limit(self):
        key = self._get_api_key() or self.client_address[0]
        return self.limiter.allow(key)

    def do_GET(self):
        METRICS["requests_total"] += 1
        if not self._check_rate_limit():
            return self._send_json(429, {"error": "rate limited"})
        path = urlparse(self.path).path
        if path == "/openapi.json":
            return self._send_json(200, OPENAPI)
        if path == "/metrics":
            return self._send_json(200, dict(METRICS))
        m = re.match(r"^/api/v1/urls/([^/]+)/analytics$", path)
        if m:
            return self._handle_analytics(m.group(1))
        m = re.match(r"^/api/v1/urls/([^/]+)$", path)
        if m:
            return self._handle_get_url(m.group(1))
        m = re.match(r"^/([^/]+)$", path)
        if m:
            return self._handle_redirect(m.group(1))
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if not self._check_rate_limit():
            return self._send_json(429, {"error": "rate limited"})
        path = urlparse(self.path).path
        try:
            if path == "/api/v1/urls":
                return self._handle_create_url()
            if path == "/api/v1/auth/register":
                return self._handle_register()
            if path == "/api/v1/auth/login":
                return self._handle_login()
        except json.JSONDecodeError:
            return self._send_json(400, {"error": "invalid json"})
        self._send_json(404, {"error": "not found"})

    def do_DELETE(self):
        if not self._check_rate_limit():
            return self._send_json(429, {"error": "rate limited"})
        m = re.match(r"^/api/v1/urls/([^/]+)$", urlparse(self.path).path)
        if not m:
            return self._send_json(404, {"error": "not found"})
        code = m.group(1)
        rows = self.db.query("SELECT * FROM url_mappings WHERE short_code=?", (code,))
        if not rows:
            return self._send_json(404, {"error": "not found"})
        row = rows[0]
        owner_id = auth.authenticate(self.db, self._get_api_key())
        if row["owner_id"] and row["owner_id"] != owner_id:
            return self._send_json(403, {"error": "forbidden"})
        self.db.execute("UPDATE url_mappings SET is_active=0 WHERE short_code=?", (code,))
        self.cache.invalidate(code)
        self._send_json(200, {"short_code": code, "is_active": False})

    def _handle_create_url(self):
        body = self._read_json()
        long_url = body.get("long_url")
        try:
            validation.validate_url(long_url)
        except validation.ValidationError as e:
            return self._send_json(400, {"error": str(e)})
        owner_id = auth.authenticate(self.db, self._get_api_key())
        custom_alias = body.get("custom_alias")
        if custom_alias:
            if not shortcode.validate_alias(custom_alias):
                return self._send_json(400, {"error": "invalid alias"})
            if self.db.query("SELECT 1 FROM url_mappings WHERE short_code=?", (custom_alias,)):
                return self._send_json(409, {"error": "alias taken"})
            code, is_custom = custom_alias, 1
        else:
            code, is_custom = shortcode.generate_code(self.db), 0
        now = _now()
        ttl = body.get("ttl_seconds")
        expires_at = (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + float(ttl)))
            if ttl else None
        )
        self.db.execute(
            "INSERT INTO url_mappings(short_code, long_url, owner_id, custom_alias, "
            "created_at, expires_at, is_active, click_count) VALUES (?,?,?,?,?,?,1,0)",
            (code, long_url, owner_id, is_custom, now, expires_at),
        )
        self.cache.set(code, long_url)
        host = self.headers.get("Host", "localhost")
        self._send_json(201, {
            "short_code": code,
            "short_url": f"http://{host}/{code}",
            "long_url": long_url,
            "expires_at": expires_at,
            "created_at": now,
        })

    def _handle_redirect(self, code):
        METRICS["redirects_total"] += 1
        long_url = self.cache.get(code)
        if long_url is None:
            rows = self.db.query("SELECT * FROM url_mappings WHERE short_code=?", (code,))
            if not rows:
                return self._send_json(404, {"error": "not found"})
            row = rows[0]
            if not row["is_active"]:
                return self._send_json(410, {"error": "gone"})
            if row["expires_at"] and row["expires_at"] < _now():
                self.db.execute("UPDATE url_mappings SET is_active=0 WHERE short_code=?", (code,))
                return self._send_json(410, {"error": "expired"})
            long_url = row["long_url"]
            self.cache.set(code, long_url)
        self.db.execute(
            "UPDATE url_mappings SET click_count = click_count + 1 WHERE short_code=?", (code,)
        )
        ua = self.headers.get("User-Agent", "")
        device = "mobile" if "Mobile" in ua else "desktop"
        browser = "chrome" if "Chrome" in ua else ("firefox" if "Firefox" in ua else "other")
        ip_hash = hashlib.sha256(self.client_address[0].encode()).hexdigest()[:16]
        self.db.execute(
            "INSERT INTO click_events(short_code, timestamp, referrer, country_code, "
            "device_type, browser, ip_hash) VALUES (?,?,?,?,?,?,?)",
            (code, _now(), self.headers.get("Referer"), "XX", device, browser, ip_hash),
        )
        self.send_response(302)
        self.send_header("Location", long_url)
        self.end_headers()

    def _handle_get_url(self, code):
        rows = self.db.query("SELECT * FROM url_mappings WHERE short_code=?", (code,))
        if not rows:
            return self._send_json(404, {"error": "not found"})
        row = rows[0]
        self._send_json(200, {
            "short_code": row["short_code"],
            "long_url": row["long_url"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "is_active": bool(row["is_active"]),
            "click_count": row["click_count"],
        })

    def _handle_analytics(self, code):
        rows = self.db.query("SELECT * FROM url_mappings WHERE short_code=?", (code,))
        if not rows:
            return self._send_json(404, {"error": "not found"})
        total = rows[0]["click_count"]
        events = self.db.query("SELECT * FROM click_events WHERE short_code=?", (code,))
        by_day, by_ref, by_country, by_device = (defaultdict(int) for _ in range(4))
        for e in events:
            by_day[e["timestamp"][:10]] += 1
            by_ref[e["referrer"] or "direct"] += 1
            by_country[e["country_code"] or "unknown"] += 1
            by_device[e["device_type"] or "unknown"] += 1
        self._send_json(200, {
            "short_code": code,
            "total_clicks": total,
            "clicks_by_day": [{"day": k, "count": v} for k, v in sorted(by_day.items())],
            "top_referrers": sorted(
                [{"referrer": k, "count": v} for k, v in by_ref.items()],
                key=lambda x: -x["count"],
            ),
            "clicks_by_country": [{"country": k, "count": v} for k, v in by_country.items()],
            "clicks_by_device": [{"device": k, "count": v} for k, v in by_device.items()],
        })

    def _handle_register(self):
        body = self._read_json()
        username, password = body.get("username"), body.get("password")
        if not username or not password:
            return self._send_json(400, {"error": "username and password required"})
        try:
            user_id, api_key = auth.register_user(self.db, username, password)
        except ValueError as e:
            return self._send_json(409, {"error": str(e)})
        self._send_json(201, {"user_id": user_id, "username": username, "api_key": api_key})

    def _handle_login(self):
        body = self._read_json()
        try:
            user_id, api_key = auth.login_user(self.db, body.get("username"), body.get("password"))
        except ValueError as e:
            return self._send_json(401, {"error": str(e)})
        self._send_json(200, {"user_id": user_id, "api_key": api_key})


def build_server(db, port=0, host="localhost", rate=200, per=1.0, cache_ttl=30):
    """Construct and bind an HTTPServer wired to db/cache/rate-limiter."""
    bound = type("BoundHandler", (Handler,), {
        "db": db,
        "cache": TTLCache(maxsize=5000, ttl=cache_ttl),
        "limiter": RateLimiter(rate=rate, per=per),
    })
    httpd = HTTPServer((host, port), bound)
    stop_event = threading.Event()
    threading.Thread(target=sweep_expired, args=(db, 30, stop_event), daemon=True).start()
    httpd.stop_sweeper = stop_event
    return httpd
