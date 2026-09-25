"""Environment-driven configuration for the URL shortener."""

from __future__ import annotations

import os

BASE_URL = os.environ.get("SHORTENER_BASE_URL", "http://localhost:8000")
STORE_BACKEND = os.environ.get("SHORTENER_STORE", "memory")  # "memory" | "sqlite"
DB_PATH = os.environ.get("SHORTENER_DB_PATH", "shortener.db")
HOST = os.environ.get("SHORTENER_HOST", "127.0.0.1")
PORT = int(os.environ.get("SHORTENER_PORT", "8000"))

# -- Rate limiting -----------------------------------------------------------
# All of these are externalized so limits can be tuned without a redeploy.
# SHORTENER_RATE_LIMIT_CONFIG_PATH may point at a JSON file (polled every
# SHORTENER_RATE_LIMIT_CONFIG_POLL_SECONDS) that overrides the env-derived
# defaults below at runtime, e.g.:
#   {"rules": {"create": {"limit": 50, "window_seconds": 60}},
#    "fail_mode": "open", "trusted_keys": ["abc123"], "trusted_multiplier": 5}
RATE_LIMIT_CREATE_LIMIT = int(os.environ.get("SHORTENER_RATE_LIMIT_CREATE", "100"))
RATE_LIMIT_CREATE_WINDOW = float(os.environ.get("SHORTENER_RATE_LIMIT_CREATE_WINDOW", "60"))
RATE_LIMIT_REDIRECT_LIMIT = int(os.environ.get("SHORTENER_RATE_LIMIT_REDIRECT", "300"))
RATE_LIMIT_REDIRECT_WINDOW = float(os.environ.get("SHORTENER_RATE_LIMIT_REDIRECT_WINDOW", "60"))
# Fail-open (default) allows traffic through if the limiter backend errors;
# fail-closed rejects it instead. See docs/ARCHITECTURE.md.
RATE_LIMIT_FAIL_MODE = os.environ.get("SHORTENER_RATE_LIMIT_FAIL_MODE", "open")
RATE_LIMIT_CONFIG_PATH = os.environ.get("SHORTENER_RATE_LIMIT_CONFIG_PATH", "")
RATE_LIMIT_CONFIG_POLL_SECONDS = float(os.environ.get("SHORTENER_RATE_LIMIT_CONFIG_POLL_SECONDS", "5"))

# Trusted API keys get a higher (multiplier) rate limit than anonymous
# clients. Keys are only ever compared against this server-side set; a
# client-supplied header is never itself treated as an identity/quota key
# unless it matches an entry here.
TRUSTED_API_KEYS = frozenset(filter(None, os.environ.get("SHORTENER_TRUSTED_API_KEYS", "").split(",")))
TRUSTED_RATE_LIMIT_MULTIPLIER = float(os.environ.get("SHORTENER_TRUSTED_RATE_LIMIT_MULTIPLIER", "5"))
