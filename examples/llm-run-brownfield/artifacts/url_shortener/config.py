"""Environment-driven configuration for the URL shortener."""

from __future__ import annotations

import os

BASE_URL = os.environ.get("SHORTENER_BASE_URL", "http://localhost:8000")
STORE_BACKEND = os.environ.get("SHORTENER_STORE", "memory")  # "memory" | "sqlite"
DB_PATH = os.environ.get("SHORTENER_DB_PATH", "shortener.db")
HOST = os.environ.get("SHORTENER_HOST", "127.0.0.1")
PORT = int(os.environ.get("SHORTENER_PORT", "8000"))

# -- rate limiting --------------------------------------------------------
# Tight limit for link creation, looser limits for redirects/lookups and the
# admin/analytics surface. All configurable without a code change.
RATE_LIMIT_SHORTEN_LIMIT = int(os.environ.get("SHORTENER_RATE_LIMIT_SHORTEN", "10"))
RATE_LIMIT_SHORTEN_WINDOW = float(os.environ.get("SHORTENER_RATE_WINDOW_SHORTEN", "60"))

RATE_LIMIT_REDIRECT_LIMIT = int(os.environ.get("SHORTENER_RATE_LIMIT_REDIRECT", "100"))
RATE_LIMIT_REDIRECT_WINDOW = float(os.environ.get("SHORTENER_RATE_WINDOW_REDIRECT", "60"))

RATE_LIMIT_ADMIN_LIMIT = int(os.environ.get("SHORTENER_RATE_LIMIT_ADMIN", "30"))
RATE_LIMIT_ADMIN_WINDOW = float(os.environ.get("SHORTENER_RATE_WINDOW_ADMIN", "60"))

# Comma-separated list of peer addresses allowed to set X-Forwarded-For (e.g.
# a reverse proxy/load balancer). Empty by default: the direct connection IP
# is used and X-Forwarded-For is ignored, to avoid trivial spoofing.
TRUSTED_PROXIES = {
    addr.strip() for addr in os.environ.get("SHORTENER_TRUSTED_PROXIES", "").split(",") if addr.strip()
}
