"""Business rules engine combining format validation, blacklist and velocity checks."""
import json
import uuid
import datetime

from . import validation

HARD_FAIL_REASONS = {
    "invalid_card_number", "expired_card", "invalid_cvv",
    "invalid_amount", "unsupported_currency", "blacklisted",
}


def _utcnow_naive():
    """Return a timezone-naive UTC datetime without using the deprecated utcnow()."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def process_transaction(payload, config, storage):
    request_id = str(uuid.uuid4())
    try:
        tx = validation.sanitize_payload(payload)
    except ValueError as exc:
        return {"error": "validation_error", "message": str(exc)}, 400

    idempotency_key = tx.get("idempotency_key")
    if idempotency_key:
        cached = storage.get_idempotent(idempotency_key, config.idempotency_ttl_seconds)
        if cached:
            resp_json, status_code = cached
            return json.loads(resp_json), status_code

    pan = tx["card_number"]
    network = validation.detect_network(pan)
    masked = validation.mask_pan(pan)
    token_hash = validation.hash_token(pan)

    reasons = []
    if not validation.luhn_check(pan):
        reasons.append("invalid_card_number")
    if not validation.validate_expiry(tx["expiry_month"], tx["expiry_year"]):
        reasons.append("expired_card")
    if not validation.validate_cvv(tx["cvv"], network):
        reasons.append("invalid_cvv")
    if not validation.validate_amount(tx["amount_cents"], config.min_amount_cents, config.max_amount_cents):
        reasons.append("invalid_amount")
    if not validation.validate_currency(tx["currency"], config.allowed_currencies):
        reasons.append("unsupported_currency")
    if storage.is_blacklisted(token_hash):
        reasons.append("blacklisted")

    hard_fail = any(r in HARD_FAIL_REASONS for r in reasons)

    limit_reasons = []
    if not hard_fail:
        now = _utcnow_naive()
        daily_key = "D:" + now.strftime("%Y-%m-%d")
        monthly_key = "M:" + now.strftime("%Y-%m")
        daily_amount, _ = storage.get_velocity(token_hash, daily_key)
        monthly_amount, _ = storage.get_velocity(token_hash, monthly_key)
        amount = tx["amount_cents"]
        if daily_amount + amount > config.daily_limit_cents:
            limit_reasons.append("daily_limit_exceeded")
        if monthly_amount + amount > config.monthly_limit_cents:
            limit_reasons.append("monthly_limit_exceeded")
        storage.add_velocity(token_hash, daily_key, amount)
        storage.add_velocity(token_hash, monthly_key, amount)

    all_reasons = reasons + limit_reasons
    if hard_fail:
        status = "rejected"
    elif limit_reasons:
        status = "flagged"
    else:
        status = "approved"

    transaction_id = tx.get("transaction_id") or request_id
    response = {
        "transaction_id": transaction_id,
        "status": status,
        "reasons": all_reasons,
        "card_network": network,
        "masked_pan": masked,
        "validated_at": _utcnow_naive().isoformat() + "Z",
    }

    if idempotency_key:
        storage.put_idempotent(idempotency_key, json.dumps(response), 200)

    storage.write_audit(request_id, token_hash, masked, status, all_reasons,
                         tx["amount_cents"], tx["currency"])

    return response, 200
