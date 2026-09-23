"""Environment-driven configuration for the URL shortener."""

from __future__ import annotations

import os

BASE_URL = os.environ.get("SHORTENER_BASE_URL", "http://localhost:8000")
STORE_BACKEND = os.environ.get("SHORTENER_STORE", "memory")  # "memory" | "sqlite"
DB_PATH = os.environ.get("SHORTENER_DB_PATH", "shortener.db")
