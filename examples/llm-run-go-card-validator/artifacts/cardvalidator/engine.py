"""Validation engine orchestrating format/limit/fraud rule checks."""
import time
from datetime import datetime, timezone

from .masking import sanitize_pan, luhn_check, detect_network, mask_card, card_token


class ValidationEngine:
    def __init__(self, rules_config, store, salt="cardvalidator_default_salt"):
        self.rules_config = rules_config
        self.store = store
        self.salt = salt

    def validate(self, txn):
        start = time.time()
        reasons = []
        trace = []
        rules = self.rules_config.get()

        pan = sanitize_pan(txn.get("card_number", ""))
        luhn_ok = bool(pan) and luhn_check(pan) and len(pan) in (13, 15, 16, 19)
        trace.append({"rule": "luhn_format", "pass": luhn_ok})
        if not luhn_ok:
            reasons.append("INVALID_CARD_FORMAT")

        network = detect_network(pan) if pan else "UNKNOWN"
        network_ok = network in rules.get("allowed_networks", [])
        trace.append({"rule": "network_allowed", "pass": network_ok})
        if not network_ok:
            reasons.append("UNSUPPORTED_NETWORK")

        try:
            mm = int(txn.get("expiry_month", 0))
            yy = int(txn.get("expiry_year", 0))
            now_dt = datetime.now(timezone.utc)
            expiry_ok = (1 <= mm <= 12) and ((yy > now_dt.year) or (yy == now_dt.year and mm >= now_dt.month))
        except (TypeError, ValueError):
            expiry_ok = False
        trace.append({"rule": "expiry_valid", "pass": expiry_ok})
        if not expiry_ok:
            reasons.append("EXPIRED_CARD")

        cvv = str(txn.get("cvv", ""))
        cvv_len = 4 if network == "AMEX" else 3
        cvv_ok = cvv.isdigit() and len(cvv) == cvv_len
        trace.append({"rule": "cvv_format", "pass": cvv_ok})
        if not cvv_ok:
            reasons.append("INVALID_CVV")

        try:
            amount = float(txn.get("amount", 0))
        except (TypeError, ValueError):
            amount = -1.0
        trace.append({"rule": "amount_limits", "pass": rules["min_amount"] <= amount <= rules["max_amount"]})
        if amount < rules["min_amount"]:
            reasons.append("AMOUNT_TOO_LOW")
        if amount > rules["max_amount"]:
            reasons.append("AMOUNT_TOO_HIGH")

        token = card_token(pan, self.salt) if pan else "unknown_token"
        now_ts = time.time()

        daily_sum = self.store.sum_amount_since(token, now_ts - 86400, now_ts) + amount
        monthly_sum = self.store.sum_amount_since(token, now_ts - 2592000, now_ts) + amount
        daily_ok = daily_sum <= rules["daily_cap"]
        monthly_ok = monthly_sum <= rules["monthly_cap"]
        trace.append({"rule": "daily_cap", "pass": daily_ok})
        trace.append({"rule": "monthly_cap", "pass": monthly_ok})
        if not daily_ok:
            reasons.append("DAILY_CAP_EXCEEDED")
        if not monthly_ok:
            reasons.append("MONTHLY_CAP_EXCEEDED")

        blacklisted = self.store.is_blacklisted(token)
        trace.append({"rule": "blacklist_check", "pass": not blacklisted})
        if blacklisted:
            reasons.append("BLACKLISTED")

        velocity_records = self.store.get_velocity(token, rules["velocity_window_seconds"], now_ts)
        velocity_ok = len(velocity_records) < rules["velocity_max_count"]
        trace.append({"rule": "velocity_check", "pass": velocity_ok})
        if not velocity_ok:
            reasons.append("VELOCITY_EXCEEDED")

        suspicious = amount >= rules["suspicious_amount_threshold"]
        trace.append({"rule": "suspicious_amount", "pass": not suspicious})
        if suspicious:
            reasons.append("SUSPICIOUS_AMOUNT")

        if pan:
            self.store.record_velocity(token, now_ts, amount)

        decision = "APPROVED" if not reasons else "DECLINED"
        latency_ms = (time.time() - start) * 1000
        masked = mask_card(pan) if pan else {"bin": "", "last4": "", "network": "UNKNOWN"}

        return {
            "transaction_id": txn.get("transaction_id"),
            "decision": decision,
            "reason_codes": reasons,
            "masked_card": masked,
            "rule_trace": trace,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "latency_ms": round(latency_ms, 3),
            "card_token": token,
        }
