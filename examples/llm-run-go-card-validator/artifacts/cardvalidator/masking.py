"""PAN masking: immediately reduce a raw PAN to non-reversible/masked fields.

Nothing downstream (logs, storage) ever sees the raw PAN or CVV.
"""

import hashlib


def mask_pan(pan: str, salt: str) -> dict:
    """Return bin (first 6), last4, a display-masked string, and a salted sha256 hash."""
    bin_ = pan[:6]
    last4 = pan[-4:]
    middle_len = max(len(pan) - 10, 0)
    masked = f"{bin_}{'*' * middle_len}{last4}"
    hashed = hashlib.sha256((pan + salt).encode("utf-8")).hexdigest()
    return {"bin": bin_, "last4": last4, "masked": masked, "hash": hashed}
