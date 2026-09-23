"""Business logic tying storage, cache, id generation, and analytics."""
import threading
import uuid
from datetime import datetime, timezone

from .storage import MemoryStore, Cache, IdGenerator, UrlMapping
from .analytics import AnalyticsQueue, AnalyticsProcessor, ClickEvent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class URLService:
    """Stateless facade used by the API/redirect tier."""

    def __init__(self):
        self.store = MemoryStore()
        self.cache = Cache(self.store)
        self.idgen = IdGenerator()
        self.queue = AnalyticsQueue()
        self.processor = AnalyticsProcessor(self.queue)
        self._lock = threading.Lock()

    def create_url(self, long_url, custom_alias=None, expires_at=None, owner_id=None):
        code = custom_alias if custom_alias else self.idgen.next_code()
        if custom_alias and self.cache.get(code):
            raise ValueError("alias already taken")
        now = _now()
        mapping = UrlMapping(
            short_code=code, long_url=long_url, created_at=now, updated_at=now,
            expires_at=expires_at, owner_id=owner_id, is_active=True,
            custom_alias=bool(custom_alias),
        )
        self.cache.put(code, mapping)
        return mapping

    def get_url(self, code):
        return self.cache.get(code)

    def update_url(self, code, long_url=None, is_active=None):
        mapping = self.cache.get(code)
        if not mapping:
            return None
        if long_url is not None:
            mapping.long_url = long_url
        if is_active is not None:
            mapping.is_active = is_active
        mapping.updated_at = _now()
        self.cache.put(code, mapping)
        return mapping

    def delete_url(self, code):
        mapping = self.cache.get(code)
        if not mapping:
            return False
        self.store.delete(code)
        self.cache.invalidate(code)
        return True

    def redirect(self, code, ip="0.0.0.0", user_agent="", referrer="", country="unknown"):
        mapping = self.cache.get(code)
        if not mapping or not mapping.is_active:
            return None
        with self._lock:
            mapping.click_count_cache += 1
        event = ClickEvent(
            event_id=str(uuid.uuid4()), short_code=code, timestamp=_now(),
            ip_hash=str(hash(ip)), user_agent=user_agent, referrer=referrer,
            country=country, device_type="mobile" if "Mobile" in user_agent else "desktop",
            is_bot="bot" in user_agent.lower(),
        )
        self.queue.push(event)
        return mapping.long_url

    def get_analytics(self, code):
        mapping = self.cache.get(code)
        if not mapping:
            return None
        result = self.processor.get_analytics(code)
        result["total_clicks"] = max(result["total_clicks"], mapping.click_count_cache)
        return result

    def health(self):
        return {"status": "ok", "cache": "ok", "db": "ok", "queue": "ok"}
