"""Base62 integer<->string codec used to turn a numeric id into a short slug.

Base62 (0-9, A-Z, a-z) packs the most information into URL-safe characters, so a
7-character code addresses ~3.5 trillion links while staying human-typeable.
"""

from __future__ import annotations

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
BASE = len(ALPHABET)
_INDEX = {ch: i for i, ch in enumerate(ALPHABET)}


def encode(number: int) -> str:
    """Encode a non-negative integer to a base62 string."""

    if number < 0:
        raise ValueError("only non-negative integers can be encoded")
    if number == 0:
        return ALPHABET[0]
    chars = []
    while number:
        number, remainder = divmod(number, BASE)
        chars.append(ALPHABET[remainder])
    return "".join(reversed(chars))


def decode(text: str) -> int:
    """Decode a base62 string back to its integer value."""

    if not text:
        raise ValueError("cannot decode an empty string")
    number = 0
    for ch in text:
        if ch not in _INDEX:
            raise ValueError(f"invalid base62 character: {ch!r}")
        number = number * BASE + _INDEX[ch]
    return number
