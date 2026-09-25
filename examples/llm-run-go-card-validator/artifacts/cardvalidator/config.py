"""Configuration loading from environment variables with sane in-code defaults."""

import os
from dataclasses import dataclass, field
from typing import List


def _list_from_env(name: str, default: List[str]) -> List[str]:
    val = os.environ.get(name)
    if val:
        return [x.strip().upper() for x in val.split(",") if x.strip()]
    return default


@dataclass
class Config:
    """Runtime configuration. Values can be overridden via env vars or kwargs."""

    min_amount: float = float(os.environ.get("CV_MIN_AMOUNT", "0.50"))
    max_amount: float = float(os.environ.get("CV_MAX_AMOUNT", "5000.00"))
    supported_currencies: List[str] = field(
        default_factory=lambda: _list_from_env("CV_SUPPORTED_CURRENCIES", ["USD", "EUR", "GBP"])
    )
    supported_networks: List[str] = field(
        default_factory=lambda: _list_from_env(
            "CV_SUPPORTED_NETWORKS", ["VISA", "MASTERCARD", "AMEX", "DISCOVER"]
        )
    )
    velocity_window_seconds: int = int(os.environ.get("CV_VELOCITY_WINDOW_SECONDS", "60"))
    velocity_max_attempts: int = int(os.environ.get("CV_VELOCITY_MAX_ATTEMPTS", "5"))
    api_keys: List[str] = field(
        default_factory=lambda: _list_from_env("CV_API_KEYS", ["dev-key-123"])
    )
    pan_salt: str = os.environ.get("CV_PAN_SALT", "change-me-salt")
    db_path: str = os.environ.get("CV_DB_PATH", "cardvalidator.db")
    host: str = os.environ.get("CV_HOST", "127.0.0.1")
    port: int = int(os.environ.get("CV_PORT", "8080"))
