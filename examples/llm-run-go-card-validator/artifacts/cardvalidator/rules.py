"""Hot-reloadable rules configuration."""
import json
import os
from datetime import datetime, timezone

DEFAULT_RULES = {
    "min_amount": 1.0,
    "max_amount": 5000.0,
    "daily_cap": 10000.0,
    "monthly_cap": 50000.0,
    "allowed_networks": ["VISA", "MASTERCARD", "AMEX", "DISCOVER"],
    "velocity_window_seconds": 60,
    "velocity_max_count": 5,
    "suspicious_amount_threshold": 3000.0,
    "blacklisted_tokens": [],
}


class RulesConfig:
    def __init__(self, path=None):
        self.path = path
        self.version = 0
        self.loaded_at = None
        self.rules = dict(DEFAULT_RULES)
        self.load()

    def load(self):
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.rules.update(data)
            except (OSError, json.JSONDecodeError):
                pass
        self.version += 1
        self.loaded_at = datetime.now(timezone.utc).isoformat()

    def reload(self):
        self.load()
        return self.version

    def get(self):
        return dict(self.rules)
