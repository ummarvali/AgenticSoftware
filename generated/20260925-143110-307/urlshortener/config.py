"""Configuration for the URL shortener service, sourced from environment variables.

No endpoint changes configuration or security controls at runtime; every
tunable (rate limits, denylist, TTLs, cache size) is set here via environment
variables at process start, per the project's security policy.
"""
import os


class Config:
    """Holds runtime configuration. Values are read from environment variables at
    construction time; attributes may also be overridden directly (used by tests)."""

    def __init__(self):
        self.host = os.environ.get("URLSHORTENER_HOST", "127.0.0.1")
        self.port = int(os.environ.get("URLSHORTENER_PORT", "8080"))
        self.db_path = os.environ.get("URLSHORTENER_DB_PATH", "urlshortener.db")
        self.base_url = os.environ.get(
            "URLSHORTENER_BASE_URL", f"http://{self.host}:{self.port}"
        )
        self.https_only = os.environ.get("URLSHORTENER_HTTPS_ONLY", "false").lower() == "true"
        self.max_url_length = int(os.environ.get("URLSHORTENER_MAX_URL_LENGTH", "2048"))
        self.default_rate_limit_per_min = int(
            os.environ.get("URLSHORTENER_DEFAULT_RATE_LIMIT", "60")
        )
        self.anonymous_rate_limit_per_min = int(
            os.environ.get("URLSHORTENER_ANON_RATE_LIMIT", "20")
        )
        self.key_creation_rate_limit_per_min = int(
            os.environ.get("URLSHORTENER_KEY_CREATE_RATE_LIMIT", "5")
        )
        self.cache_capacity = int(os.environ.get("URLSHORTENER_CACHE_CAPACITY", "10000"))
        self.sweeper_interval_seconds = int(os.environ.get("URLSHORTENER_SWEEP_INTERVAL", "60"))
        self.short_code_length = int(os.environ.get("URLSHORTENER_SHORT_CODE_LENGTH", "7"))
        raw_denylist = os.environ.get(
            "URLSHORTENER_DENYLIST_HOSTS", "localhost,127.0.0.1,0.0.0.0,::1"
        )
        self.denylist_hosts = {h.strip().lower() for h in raw_denylist.split(",") if h.strip()}


def load_config():
    """Build a fresh Config instance from the current environment."""
    return Config()


config = load_config()
