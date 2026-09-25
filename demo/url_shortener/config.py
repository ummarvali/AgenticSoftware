"""Environment-driven configuration for the URL shortener."""

from __future__ import annotations

import os

BASE_URL = os.environ.get("SHORTENER_BASE_URL", "http://localhost:8000")
STORE_BACKEND = os.environ.get("SHORTENER_STORE", "memory")  # "memory" | "sqlite"
DB_PATH = os.environ.get("SHORTENER_DB_PATH", "shortener.db")
HOST = os.environ.get("SHORTENER_HOST", "127.0.0.1")
PORT = int(os.environ.get("SHORTENER_PORT", "8000"))

# Rate limiting: fixed-window counter, defaults to 60 requests/minute per client
# (IP by default, API key if an Authorization/X-API-Key header is present).
# Reconfigurable at runtime via PUT /admin/rate-limit-config without a redeploy.
RATE_LIMIT = int(os.environ.get("SHORTENER_RATE_LIMIT", "60"))
RATE_LIMIT_WINDOW_SECONDS = float(os.environ.get("SHORTENER_RATE_LIMIT_WINDOW_SECONDS", "60"))
_allowlist_raw = os.environ.get("SHORTENER_RATE_LIMIT_ALLOWLIST", "")
RATE_LIMIT_ALLOWLIST = [item.strip() for item in _allowlist_raw.split(",") if item.strip()]
