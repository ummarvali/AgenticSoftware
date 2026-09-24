"""Configuration loader reading environment variables with sane defaults."""
import os


class Config:
    def __init__(self, allowed_currencies, min_amount_cents, max_amount_cents,
                 daily_limit_cents, monthly_limit_cents, idempotency_ttl_seconds, db_path):
        self.allowed_currencies = allowed_currencies
        self.min_amount_cents = min_amount_cents
        self.max_amount_cents = max_amount_cents
        self.daily_limit_cents = daily_limit_cents
        self.monthly_limit_cents = monthly_limit_cents
        self.idempotency_ttl_seconds = idempotency_ttl_seconds
        self.db_path = db_path

    @classmethod
    def load_from_env(cls):
        currencies = os.environ.get("ALLOWED_CURRENCIES", "USD,EUR,GBP,JPY")
        allowed = {c.strip().upper() for c in currencies.split(",") if c.strip()}
        return cls(
            allowed_currencies=allowed,
            min_amount_cents=int(os.environ.get("MIN_AMOUNT_CENTS", "1")),
            max_amount_cents=int(os.environ.get("MAX_AMOUNT_CENTS", "100000000")),
            daily_limit_cents=int(os.environ.get("DAILY_LIMIT_CENTS", "500000000")),
            monthly_limit_cents=int(os.environ.get("MONTHLY_LIMIT_CENTS", "2000000000")),
            idempotency_ttl_seconds=int(os.environ.get("IDEMPOTENCY_TTL_SECONDS", "86400")),
            db_path=os.environ.get("DB_PATH", "cardvalidator.db"),
        )
