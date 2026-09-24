"""URL format validation and basic safety checks."""
from urllib.parse import urlparse

ALLOWED_SCHEMES = {"http", "https"}
BLOCKED_SUBSTRINGS = ("javascript:", "data:", "<script", "file://", "vbscript:")


class ValidationError(ValueError):
    """Raised when a submitted URL fails validation."""


def validate_url(url):
    """Validate a long URL for shortening. Raises ValidationError on failure."""
    if not url or not isinstance(url, str) or len(url) > 2048:
        raise ValidationError("URL missing or too long")
    lowered = url.strip().lower()
    for bad in BLOCKED_SUBSTRINGS:
        if bad in lowered:
            raise ValidationError("URL contains disallowed content")
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValidationError("URL scheme must be http or https")
    if not parsed.netloc:
        raise ValidationError("URL must include a host")
    return url
