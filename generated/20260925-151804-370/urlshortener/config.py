"""Configuration loaded from environment variables.

No endpoint exists to change any of these values at runtime: security
sensitive settings (API keys, rate limits) are configuration only, per
project security policy.
"""
import os


class Config:
    def __init__(self):
        self.host = os.environ.get("HOST", "127.0.0.1")
        self.port = int(os.environ.get("PORT", "8000"))
        self.db_path = os.environ.get("DB_PATH", "urlshortener.db")
        self.base_url = os.environ.get("BASE_URL", f"http://{self.host}:{self.port}")
        # Comma separated "key:owner_name" pairs, seeded into api_keys table on startup.
        self.api_keys = os.environ.get("SHORTENER_API_KEYS", "")
        self.rate_limit_per_minute = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "120"))
        self.redirect_rate_limit_per_minute = int(
            os.environ.get("REDIRECT_RATE_LIMIT_PER_MINUTE", "1200")
        )
        self.cache_size = int(os.environ.get("CACHE_SIZE", "1000"))
        self.max_url_length = int(os.environ.get("MAX_URL_LENGTH", "2048"))


def load_config():
    return Config()
