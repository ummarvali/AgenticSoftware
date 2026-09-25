"""Structural and business rule validators.

Structural: Luhn check, PAN length/network match, expiry, CVV length-by-network.
Business: amount limits, supported currency/network, merchant ID format.
"""

from datetime import datetime

NETWORK_PAN_LENGTHS = {
    "VISA": (13, 16, 19),
    "MASTERCARD": (16,),
    "AMEX": (15,),
    "DISCOVER": (16, 19),
}

NETWORK_CVV_LENGTHS = {
    "VISA": 3,
    "MASTERCARD": 3,
    "AMEX": 4,
    "DISCOVER": 3,
}


def luhn_check(pan: str) -> bool:
    """Standard Luhn checksum over a digit string."""
    digits = [int(c) for c in pan]
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def detect_network(pan: str) -> str:
    """Detect card network from BIN prefix. Returns None if unrecognized."""
    if not pan.isdigit() or len(pan) < 2:
        return None
    if pan.startswith("4"):
        return "VISA"
    two = int(pan[:2])
    four = int(pan[:4]) if len(pan) >= 4 else -1
    if 51 <= two <= 55 or 2221 <= four <= 2720:
        return "MASTERCARD"
    if pan[:2] in ("34", "37"):
        return "AMEX"
    if pan.startswith("6011") or pan.startswith("65") or pan[:3] in (
        "644", "645", "646", "647", "648", "649",
    ):
        return "DISCOVER"
    return None


def validate_structural(pan: str, expiry_month: int, expiry_year: int, cvv: str, network_hint):
    """Run structural checks. Returns (list_of_reason_codes, detected_network_or_None)."""
    reasons = []
    network = detect_network(pan)

    if network is None:
        reasons.append("UNKNOWN_NETWORK")
    if network_hint and network and str(network_hint).upper() != network:
        reasons.append("NETWORK_MISMATCH")

    if not pan.isdigit():
        reasons.append("PAN_FORMAT_INVALID")
    elif network and len(pan) not in NETWORK_PAN_LENGTHS.get(network, ()):
        reasons.append("PAN_LENGTH_INVALID")

    if pan.isdigit() and not luhn_check(pan):
        reasons.append("LUHN_FAILED")

    if not (1 <= expiry_month <= 12):
        reasons.append("EXPIRY_FORMAT_INVALID")
    else:
        exp_year_full = expiry_year if expiry_year > 99 else 2000 + expiry_year
        now = datetime.utcnow()
        if (exp_year_full, expiry_month) < (now.year, now.month):
            reasons.append("CARD_EXPIRED")

    if not cvv.isdigit():
        reasons.append("CVV_FORMAT_INVALID")
    elif network and len(cvv) != NETWORK_CVV_LENGTHS.get(network, 3):
        reasons.append("CVV_LENGTH_INVALID")

    return reasons, network


def validate_business(amount: float, currency: str, merchant_id: str, network, config):
    """Run business rule checks. Returns list of reason codes."""
    reasons = []
    if amount is None or amount <= 0:
        reasons.append("AMOUNT_INVALID")
    elif amount < config.min_amount or amount > config.max_amount:
        reasons.append("AMOUNT_OUT_OF_RANGE")

    if not currency or currency.upper() not in config.supported_currencies:
        reasons.append("CURRENCY_UNSUPPORTED")

    if network and network not in config.supported_networks:
        reasons.append("NETWORK_UNSUPPORTED")

    if not merchant_id or not _valid_merchant_id(merchant_id):
        reasons.append("MERCHANT_ID_INVALID")

    return reasons


def _valid_merchant_id(merchant_id: str) -> bool:
    if not (3 <= len(merchant_id) <= 32):
        return False
    return all(c.isalnum() or c in "_-" for c in merchant_id)
