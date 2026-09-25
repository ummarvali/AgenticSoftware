"""Application/routing layer: request parsing, auth, and the validation pipeline.

Decoupled from the HTTP transport so it can be unit-tested directly and
driven by http.server (see server.py) without duplicating logic.
"""

import json
import logging
import re
import uuid
from datetime import datetime, timezone

from .config import Config
from .fraud import FraudEngine
from .masking import mask_pan
from .storage import Storage
from .metrics import Metrics
from .validators import validate_business, validate_structural

logger = logging.getLogger("cardvalidator")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

REQUIRED_FIELDS = ["pan", "expiry_month", "expiry_year", "cvv", "amount", "currency", "merchant_id"]
ALLOWED_FIELDS = set(REQUIRED_FIELDS + ["network_hint"])

PAN_RE = re.compile(r"^\d{12,19}$")
CVV_RE = re.compile(r"^\d{3,4}$")
HASH_RE = re.compile(r"^[a-f0-9]{64}$")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class Application:
    """Holds config/storage/fraud/metrics singletons and dispatches requests."""

    def __init__(self, config: Config = None, storage: Storage = None):
        self.config = config or Config()
        self.storage = storage or Storage(self.config.db_path)
        self.fraud = FraudEngine(self.config, self.storage)
        self.metrics = Metrics()

    def close(self):
        self.storage.close()

    def handle(self, method: str, path: str, headers, body: str):
        """Route a request. Returns (status_code, payload, content_type)."""
        self.metrics.inc("requests_total")
        try:
            if path == "/healthz" and method == "GET":
                return 200, {"status": "ok"}, "application/json"

            if path == "/readyz" and method == "GET":
                db_ok = self.storage.check_connectivity()
                status = 200 if db_ok else 503
                return status, {
                    "status": "ready" if db_ok else "not_ready",
                    "checks": {"db": "ok" if db_ok else "fail", "config": "ok"},
                }, "application/json"

            if path == "/metrics" and method == "GET":
                return 200, self.metrics.render(), "text/plain"

            if path == "/v1/validate" and method == "POST":
                self._check_auth(headers)
                return self._handle_validate(body)

            if path == "/v1/blacklist" and method == "POST":
                self._check_auth(headers)
                return self._handle_blacklist(body)

            if path.startswith("/v1/audit/") and method == "GET":
                self._check_auth(headers)
                request_id = path[len("/v1/audit/"):]
                return self._handle_audit_get(request_id)

            return 404, {"error": "not_found", "message": "no such route"}, "application/json"
        except ApiError as e:
            self.metrics.inc("errors_total")
            return e.status, {"error": e.code, "message": e.message}, "application/json"
        except Exception:
            logger.exception("unhandled error")
            self.metrics.inc("errors_total")
            return 500, {"error": "internal_error", "message": "internal server error"}, "application/json"

    def _check_auth(self, headers):
        key = headers.get("X-API-Key")
        if not key or key not in self.config.api_keys:
            raise ApiError(401, "unauthorized", "invalid or missing API key")

    def _parse_json(self, body: str) -> dict:
        try:
            data = json.loads(body or "{}")
        except json.JSONDecodeError:
            raise ApiError(400, "invalid_json", "request body is not valid JSON")
        if not isinstance(data, dict):
            raise ApiError(400, "invalid_payload", "payload must be a JSON object")
        return data

    def _handle_validate(self, body: str):
        data = self._parse_json(body)

        extra = set(data.keys()) - ALLOWED_FIELDS
        if extra:
            raise ApiError(400, "unexpected_fields", f"unexpected fields: {sorted(extra)}")
        missing = [f for f in REQUIRED_FIELDS if f not in data]
        if missing:
            raise ApiError(400, "missing_fields", f"missing fields: {missing}")

        pan = data.get("pan")
        cvv = data.get("cvv")
        currency = data.get("currency")
        merchant_id = data.get("merchant_id")
        network_hint = data.get("network_hint")

        if not isinstance(pan, str) or not PAN_RE.match(pan):
            raise ApiError(400, "pan_format_invalid", "pan must be 12-19 digits")
        if not isinstance(cvv, str) or not CVV_RE.match(cvv):
            raise ApiError(400, "cvv_format_invalid", "cvv must be 3-4 digits")
        if not isinstance(currency, str):
            raise ApiError(400, "invalid_types", "currency must be a string")
        if not isinstance(merchant_id, str):
            raise ApiError(400, "invalid_types", "merchant_id must be a string")
        if network_hint is not None and not isinstance(network_hint, str):
            raise ApiError(400, "invalid_types", "network_hint must be a string")

        try:
            expiry_month = int(data["expiry_month"])
            expiry_year = int(data["expiry_year"])
            amount = float(data["amount"])
        except (TypeError, ValueError):
            raise ApiError(400, "invalid_types", "expiry_month/expiry_year/amount must be numeric")

        request_id = str(uuid.uuid4())
        mask = mask_pan(pan, self.config.pan_salt)

        struct_reasons, network = validate_structural(pan, expiry_month, expiry_year, cvv, network_hint)
        biz_reasons = validate_business(amount, currency, merchant_id, network, self.config)
        fraud_reasons, risk_score = self.fraud.check(mask["hash"])

        reasons = struct_reasons + biz_reasons + fraud_reasons
        valid = len(reasons) == 0

        record = {
            "id": request_id,
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "masked_pan": mask["masked"],
            "bin": mask["bin"],
            "last4": mask["last4"],
            "network": network or "UNKNOWN",
            "amount": amount,
            "currency": currency.upper(),
            "merchant_id": merchant_id,
            "valid": valid,
            "reason_codes": reasons,
            "risk_score": risk_score,
        }
        self.storage.add_audit_record(record)
        logger.info(json.dumps({
            "event": "validation",
            "request_id": request_id,
            "valid": valid,
            "network": network,
            "masked_pan": mask["masked"],
            "reason_codes": reasons,
            "risk_score": risk_score,
        }))

        self.metrics.inc("validations_total")
        self.metrics.inc("validations_valid_total" if valid else "validations_invalid_total")

        response = {
            "request_id": request_id,
            "valid": valid,
            "reason_codes": reasons,
            "risk_score": risk_score,
            "network": network or "UNKNOWN",
            "masked_pan": mask["masked"],
        }
        return 200, response, "application/json"

    def _handle_blacklist(self, body: str):
        data = self._parse_json(body)
        hashed_pan = data.get("hashed_pan")
        reason = data.get("reason", "manual")

        if not isinstance(hashed_pan, str) or not HASH_RE.match(hashed_pan):
            raise ApiError(400, "invalid_hashed_pan", "hashed_pan must be a 64-char hex sha256 digest")
        if not isinstance(reason, str) or not reason:
            raise ApiError(400, "invalid_reason", "reason must be a non-empty string")

        added_at = datetime.now(timezone.utc).isoformat()
        self.storage.add_blacklist(hashed_pan, reason, added_at)
        self.metrics.inc("blacklist_added_total")
        return 201, {"hashed_pan": hashed_pan, "reason": reason, "added_at": added_at}, "application/json"

    def _handle_audit_get(self, request_id: str):
        if not request_id:
            raise ApiError(400, "invalid_request_id", "request_id is required")
        record = self.storage.get_audit_record(request_id)
        if not record:
            raise ApiError(404, "not_found", "audit record not found")
        response = {
            "request_id": record["request_id"],
            "timestamp": record["timestamp"],
            "masked_pan": record["masked_pan"],
            "network": record["network"],
            "amount": record["amount"],
            "currency": record["currency"],
            "valid": record["valid"],
            "reason_codes": record["reason_codes"],
            "risk_score": record["risk_score"],
        }
        return 200, response, "application/json"
