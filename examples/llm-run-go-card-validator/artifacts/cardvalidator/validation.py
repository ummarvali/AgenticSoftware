"""Pure validation functions: Luhn, network detection, expiry, CVV, amount, currency."""
import re
import hashlib
import datetime
import calendar

ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


def _utcnow_naive():
    """Return a timezone-naive UTC datetime without using the deprecated utcnow()."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def luhn_check(pan):
    if not pan.isdigit():
        return False
    digits = [int(d) for d in pan]
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def detect_network(pan):
    if re.match(r"^4", pan):
        return "visa"
    if re.match(r"^(5[1-5]|2[2-7])", pan):
        return "mastercard"
    if re.match(r"^3[47]", pan):
        return "amex"
    if re.match(r"^(6011|65|64[4-9])", pan):
        return "discover"
    return "unknown"


def cvv_length_for_network(network):
    return 4 if network == "amex" else 3


def validate_cvv(cvv, network):
    return cvv.isdigit() and len(cvv) == cvv_length_for_network(network)


def validate_expiry(month, year, now=None):
    now = now or _utcnow_naive()
    last_day = calendar.monthrange(year, month)[1]
    expiry = datetime.datetime(year, month, last_day, 23, 59, 59)
    return expiry >= now


def validate_amount(amount_cents, min_cents, max_cents):
    return isinstance(amount_cents, int) and min_cents <= amount_cents <= max_cents


def validate_currency(code, allowed):
    return code in allowed


def mask_pan(pan):
    """Mask a PAN keeping the first 6 and last 4 digits visible.

    The middle segment is rendered with asterisks whose count is
    ``len(pan) - 9`` (for pans of at least 10 characters), matching the
    service's documented masking convention.
    """
    if len(pan) < 10:
        return "*" * len(pan)
    star_count = max(len(pan) - 9, 0)
    return pan[:6] + ("*" * star_count) + pan[-4:]


def hash_token(pan):
    return hashlib.sha256(pan.encode("utf-8")).hexdigest()


def sanitize_payload(payload):
    """Strict type/length/charset checks on inbound fields. Raises ValueError."""
    tx = {}

    card_number = payload.get("card_number")
    if not isinstance(card_number, str) or not (12 <= len(card_number) <= 19) or not card_number.isdigit():
        raise ValueError("invalid card_number field")
    tx["card_number"] = card_number

    cvv = payload.get("cvv")
    if not isinstance(cvv, str) or not (3 <= len(cvv) <= 4) or not cvv.isdigit():
        raise ValueError("invalid cvv field")
    tx["cvv"] = cvv

    month = payload.get("expiry_month")
    year = payload.get("expiry_year")
    if not isinstance(month, int) or isinstance(month, bool) or not (1 <= month <= 12):
        raise ValueError("invalid expiry_month field")
    if not isinstance(year, int) or isinstance(year, bool) or not (2000 <= year <= 2100):
        raise ValueError("invalid expiry_year field")
    tx["expiry_month"] = month
    tx["expiry_year"] = year

    amount = payload.get("amount_cents")
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise ValueError("invalid amount_cents field")
    tx["amount_cents"] = amount

    currency = payload.get("currency")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("invalid currency field")
    tx["currency"] = currency

    idem = payload.get("idempotency_key")
    if idem is not None:
        if not isinstance(idem, str) or not ID_RE.match(idem):
            raise ValueError("invalid idempotency_key field")
    tx["idempotency_key"] = idem

    txid = payload.get("transaction_id")
    if txid is not None:
        if not isinstance(txid, str) or not ID_RE.match(txid):
            raise ValueError("invalid transaction_id field")
    tx["transaction_id"] = txid

    return tx
