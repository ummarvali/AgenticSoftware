"""Environment-driven configuration for the URL shortener."""

from __future__ import annotations

import os

BASE_URL = os.environ.get("SHORTENER_BASE_URL", "http://localhost:8000")
STORE_BACKEND = os.environ.get("SHORTENER_STORE", "memory")  # "memory" | "sqlite"
DB_PATH = os.environ.get("SHORTENER_DB_PATH", "shortener.db")
HOST = os.environ.get("SHORTENER_HOST", "127.0.0.1")
PORT = int(os.environ.get("SHORTENER_PORT", "8000"))
# Max number of link records held in the in-memory redirect-lookup cache.
CACHE_MAX_SIZE = int(os.environ.get("SHORTENER_CACHE_SIZE", "10000"))
