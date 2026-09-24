"""Base62 short code generation with collision resolution."""
import random
import string

ALPHABET = string.digits + string.ascii_lowercase + string.ascii_uppercase
_BASE = len(ALPHABET)


def encode(num):
    """Encode a non-negative integer as a base62 string."""
    if num == 0:
        return ALPHABET[0]
    digits = []
    while num:
        num, rem = divmod(num, _BASE)
        digits.append(ALPHABET[rem])
    return "".join(reversed(digits))


def generate_code(db):
    """Generate a collision-resistant short code using a monotonic counter,
    falling back to random salt if the counter-derived code collides."""
    for _ in range(5):
        n = db.next_counter("short_code")
        code = encode(n)
        if not db.query("SELECT 1 FROM url_mappings WHERE short_code=?", (code,)):
            return code
    while True:
        code = "".join(random.choice(ALPHABET) for _ in range(8))
        if not db.query("SELECT 1 FROM url_mappings WHERE short_code=?", (code,)):
            return code


def validate_alias(alias):
    """Validate a user-supplied custom alias."""
    if not alias or not (3 <= len(alias) <= 32):
        return False
    allowed = set(ALPHABET + "-_")
    return all(c in allowed for c in alias)
