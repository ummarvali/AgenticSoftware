"""Collision-resistant short code generation using base62 alphabet.

Codes are generated randomly and validated for uniqueness against the
storage layer (via a caller-supplied `exists` predicate). If collisions
persist, the code length is grown, which keeps the collision probability
astronomically low even at billions of stored URLs.
"""

import random

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
BASE = len(ALPHABET)


def encode(number):
    """Encode a non-negative integer as a base62 string."""
    if number == 0:
        return ALPHABET[0]
    digits = []
    while number:
        number, rem = divmod(number, BASE)
        digits.append(ALPHABET[rem])
    return "".join(reversed(digits))


def random_code(length):
    return "".join(random.choice(ALPHABET) for _ in range(length))


def generate_unique_code(exists, start_length=7, max_attempts_per_length=5,
                          max_length=16):
    """Return a short code for which `exists(code)` is False.

    Tries `max_attempts_per_length` random codes at each length, growing
    the length if all attempts collide (extremely unlikely in practice).
    """
    length = start_length
    while length <= max_length:
        for _ in range(max_attempts_per_length):
            candidate = random_code(length)
            if not exists(candidate):
                return candidate
        length += 1
    raise RuntimeError("unable to generate unique short code")

