"""PAN/CVV sanitization, Luhn check, network detection, masking, tokenization."""
import hashlib


def sanitize_pan(raw):
    return ''.join(ch for ch in str(raw) if ch.isdigit())


def luhn_check(pan):
    if not pan or not pan.isdigit():
        return False
    total = 0
    for i, ch in enumerate(pan[::-1]):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def detect_network(pan):
    if not pan:
        return "UNKNOWN"
    if pan.startswith("4"):
        return "VISA"
    if pan[:2] in {"51", "52", "53", "54", "55"}:
        return "MASTERCARD"
    if len(pan) >= 6:
        try:
            prefix6 = int(pan[:6])
            if 222100 <= prefix6 <= 272099:
                return "MASTERCARD"
        except ValueError:
            pass
    if pan[:2] in {"34", "37"}:
        return "AMEX"
    if pan.startswith("6011") or pan[:2] == "65":
        return "DISCOVER"
    if len(pan) >= 3:
        try:
            if 644 <= int(pan[:3]) <= 649:
                return "DISCOVER"
        except ValueError:
            pass
    return "UNKNOWN"


def mask_card(pan):
    bin_ = pan[:6] if len(pan) >= 6 else pan
    last4 = pan[-4:] if len(pan) >= 4 else pan
    return {"bin": bin_, "last4": last4, "network": detect_network(pan)}


def card_token(pan, salt="cardvalidator_default_salt"):
    h = hashlib.sha256()
    h.update((pan + salt).encode("utf-8"))
    return h.hexdigest()
