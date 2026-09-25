"""Collision-resistant short code generation (base62 random tokens)."""
import secrets

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def generate_short_code(length=7):
    """Return a random base62 string of the given length.

    Uniqueness is not guaranteed by this function alone; callers must retry
    on a UNIQUE constraint violation when persisting the code.
    """
    return "".join(secrets.choice(ALPHABET) for _ in range(length))
