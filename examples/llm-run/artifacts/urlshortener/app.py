"""Core application logic tying together storage, cache and analytics."""
import time
from datetime import datetime, timezone

from .storage import Storage
from .core import (
    encode_base62,
    validate_long_url,
    validate_custom_alias,
    validate_expiry,
    LRUCache,
    TokenBucketLimiter,
    AnalyticsPipeline,
)

DOMAIN = "https://short.ly"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Application:
    def __init__(self, db_path=":memory:"):
        self.storage = Storage(db_path)
        self.cache = LRUCache(capacity=10000)
        self.limiter = TokenBucketLimiter(rate=50, capacity=100)
        self.analytics = AnalyticsPipeline(self.storage, flush_interval=0.2)

    def create_url(self, body, owner_key=None):
        long_url = body.get("long_url") or body.get("url")
        custom_alias = body.get("custom_alias")
        expires_at = body.get("expires_at")

        if not long_url or not validate_long_url(long_url):
            return 400, {"error": "invalid long_url"}
        if expires_at is not None and not validate_expiry(expires_at):
            return 400, {"error": "invalid expires_at"}

        created_at = now_iso()

        if custom_alias:
            if not validate_custom_alias(custom_alias):
                return 400, {"error": "invalid custom_alias"}
            ok = self.storage.insert_url(
                custom_alias, long_url, owner_key, created_at, expires_at, True
            )
            if not ok:
                return 409, {"error": "alias already taken"}
            short_code = custom_alias
        else:
            short_code = None
            for attempt in range(5):
                candidate_id = self.storage.next_id() + attempt + int(time.time() * 1000) % 997
                candidate = encode_base62(candidate_id)
                if self.storage.insert_url(
                    candidate, long_url, owner_key, created_at, expires_at, False
                ):
                    short_code = candidate
                    break
            if short_code is None:
                return 500, {"error": "failed to allocate short code"}

        self.cache.set(short_code, long_url)
        return 201, {
            "short_code": short_code,
            "short_url": f"{DOMAIN}/{short_code}",
            "long_url": long_url,
            "expires_at": expires_at,
            "created_at": created_at,
        }

    def resolve(self, short_code):
        cached = self.cache.get(short_code)
        row = self.storage.get_url(short_code)
        if row is None:
            return None
        if row["expires_at"] and row["expires_at"] < now_iso():
            self.cache.invalidate(short_code)
            return "EXPIRED"
        if cached is None:
            self.cache.set(short_code, row["long_url"])
        return row["long_url"]

    def record_click(self, short_code, referrer, user_agent, geo_country="US"):
        device = "mobile" if user_agent and "Mobile" in user_agent else "desktop"
        self.analytics.record(short_code, now_iso(), referrer, user_agent, device, geo_country)

    def get_url_info(self, short_code):
        row = self.storage.get_url(short_code)
        if row is None:
            return 404, {"error": "not found"}
        owner = None
        if row["owner_key"]:
            owner = self.storage.get_api_key_owner(row["owner_key"]) or row["owner_key"]
        return 200, {
            "short_code": row["short_code"],
            "long_url": row["long_url"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "owner": owner,
            "click_count": row["click_count"],
        }

    def delete_url(self, short_code):
        ok = self.storage.delete_url(short_code)
        self.cache.invalidate(short_code)
        if not ok:
            return 404, {"error": "not found"}
        return 200, {"short_code": short_code, "status": "deleted"}

    def get_analytics(self, short_code):
        row = self.storage.get_url(short_code)
        if row is None:
            return 404, {"error": "not found"}
        self.analytics.flush_now()
        row = self.storage.get_url(short_code)
        daily = self.storage.get_daily_aggregates(short_code)
        recent = self.storage.get_recent_clicks(short_code, limit=10)
        return 200, {
            "short_code": short_code,
            "total_clicks": row["click_count"],
            "daily": daily,
            "recent_events": recent,
        }

    def shutdown(self):
        self.analytics.stop()
