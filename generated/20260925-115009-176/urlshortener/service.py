"""Domain/service layer: orchestrates validation, short code generation,
caching, persistence and analytics aggregation. This is the single place
that encodes business rules, so the HTTP layer stays thin.
"""

import datetime
import secrets
import uuid
from collections import Counter

from .shortcode import generate_unique_code
from .cache import LRUCache
from .validation import (
    ValidationError,
    RateLimiter,
    validate_long_url,
    validate_alias,
    is_blacklisted,
)


class NotFoundError(Exception):
    status = 404


class ConflictError(Exception):
    status = 409


class ForbiddenError(Exception):
    status = 403


class RateLimitError(Exception):
    status = 429


def now_iso():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value):
    try:
        cleaned = value[:-1] if value.endswith("Z") else value
        return datetime.datetime.fromisoformat(cleaned)
    except Exception:
        raise ValidationError("expires_at must be an ISO-8601 datetime string")


def parse_device(user_agent):
    if not user_agent:
        return "unknown"
    ua = user_agent.lower()
    if "bot" in ua or "spider" in ua or "crawler" in ua:
        return "bot"
    if "mobi" in ua or "android" in ua or "iphone" in ua:
        return "mobile"
    return "desktop"


class UrlShortenerService:
    def __init__(self, storage, cache=None, create_rate_limiter=None,
                 redirect_rate_limiter=None):
        self.storage = storage
        self.cache = cache or LRUCache()
        self.create_rl = create_rate_limiter or RateLimiter(capacity=30, refill_rate=5)
        self.redirect_rl = redirect_rate_limiter or RateLimiter(capacity=200, refill_rate=50)

    # -- creation -----------------------------------------------------------
    def create_short_url(self, long_url, custom_alias=None, expires_at=None,
                          owner_id=None, client_ip="unknown"):
        if not self.create_rl.allow(client_ip):
            raise RateLimitError("rate limit exceeded for URL creation")

        validate_long_url(long_url)
        patterns = self.storage.get_blacklist_patterns()
        if is_blacklisted(long_url, patterns):
            raise ValidationError("long_url is blacklisted")

        if expires_at:
            expiry_dt = parse_iso(expires_at)
            # expires_at is stored with whole-second precision (see
            # now_iso), so compare against a similarly floored "now" to
            # avoid rejecting timestamps that are a few milliseconds in
            # the future but round down to the current second.
            now_floor = datetime.datetime.utcnow().replace(microsecond=0)
            if expiry_dt < now_floor:
                raise ValidationError("expires_at must be in the future")

        if custom_alias:
            validate_alias(custom_alias)
            if self.storage.get_url(custom_alias) is not None:
                raise ConflictError("custom_alias already in use")
            short_code = custom_alias
            is_custom = True
        else:
            short_code = generate_unique_code(
                lambda c: self.storage.get_url(c) is not None
            )
            is_custom = False

        created_at = now_iso()
        self.storage.insert_url(
            short_code, long_url, owner_id, created_at, expires_at, is_custom
        )
        return {
            "short_code": short_code,
            "long_url": long_url,
            "created_at": created_at,
            "expires_at": expires_at,
        }

    # -- redirect / clicks ----------------------------------------------------
    def get_url_for_redirect(self, short_code, client_ip="unknown"):
        if not self.redirect_rl.allow(client_ip):
            raise RateLimitError("rate limit exceeded for redirects")

        row = self.cache.get(short_code)
        if row is None:
            row = self.storage.get_url(short_code)
            if row is None:
                raise NotFoundError("short code not found")
            self.cache.set(short_code, row)

        if not row["is_active"]:
            raise NotFoundError("short url is inactive")

        if row["expires_at"]:
            expiry_dt = parse_iso(row["expires_at"])
            if expiry_dt <= datetime.datetime.utcnow():
                self.storage.set_active(short_code, False)
                self.cache.invalidate(short_code)
                raise NotFoundError("short url has expired")

        return row

    def record_click(self, short_code, referrer, user_agent, country):
        self.storage.increment_click(short_code)
        self.cache.invalidate(short_code)
        self.storage.insert_click_event(
            short_code, now_iso(), referrer, user_agent, country,
            parse_device(user_agent),
        )

    # -- metadata / lifecycle -------------------------------------------------
    def get_metadata(self, short_code):
        row = self.storage.get_url(short_code)
        if row is None:
            raise NotFoundError("short code not found")
        return row

    def deactivate(self, short_code, requester_owner_id=None):
        row = self.storage.get_url(short_code)
        if row is None:
            raise NotFoundError("short code not found")
        if row["owner_id"] and row["owner_id"] != requester_owner_id:
            raise ForbiddenError("not authorized to modify this short url")
        self.storage.set_active(short_code, False)
        self.cache.invalidate(short_code)
        return short_code

    # -- analytics --------------------------------------------------------
    def get_analytics(self, short_code, requester_owner_id=None):
        row = self.storage.get_url(short_code)
        if row is None:
            raise NotFoundError("short code not found")
        if row["owner_id"] and row["owner_id"] != requester_owner_id:
            raise ForbiddenError("not authorized to view analytics")

        events = self.storage.get_click_events(short_code)
        by_day = Counter(e["ts"][:10] for e in events)
        by_ref = Counter((e["referrer"] or "direct") for e in events)
        by_geo = Counter((e["ip_country"] or "Unknown") for e in events)

        return {
            "short_code": short_code,
            "total_clicks": len(events),
            "clicks_by_day": [
                {"date": d, "count": c} for d, c in sorted(by_day.items())
            ],
            "top_referrers": [
                {"referrer": r, "count": c}
                for r, c in sorted(by_ref.items(), key=lambda x: -x[1])[:10]
            ],
            "geo_distribution": [
                {"country": g, "count": c}
                for g, c in sorted(by_geo.items(), key=lambda x: -x[1])
            ],
        }

    # -- users / auth -------------------------------------------------------
    def create_user(self):
        user_id = str(uuid.uuid4())
        api_token = secrets.token_hex(16)
        self.storage.insert_user(user_id, api_token, now_iso())
        return {"user_id": user_id, "api_token": api_token}

    def authenticate(self, token):
        if not token:
            return None
        user = self.storage.get_user_by_token(token)
        return user["user_id"] if user else None

