"""URL validation: format checks, scheme whitelist, and basic malicious-link heuristics."""
from urllib.parse import urlparse

MAX_URL_LENGTH = 2048
ALLOWED_SCHEMES = {"http", "https"}
# Minimal illustrative blocklist; a production system would use a maintained feed.
BLOCKED_DOMAINS = {"malware.test", "phishing.test", "evil.example"}


class ValidationError(ValueError):
    """Raised when submitted input fails validation."""


def validate_url(url):
    """Validate a long URL: required, bounded length, http(s) scheme, non-blocked host."""
    if not url or not isinstance(url, str):
        raise ValidationError("url is required")
    if len(url) > MAX_URL_LENGTH:
        raise ValidationError("url exceeds maximum length")
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValidationError("url scheme must be http or https")
    if not parsed.netloc:
        raise ValidationError("url must include a host")
    host = (parsed.hostname or "").lower()
    if host in BLOCKED_DOMAINS:
        raise ValidationError("url host is blocked")
    if len(parsed.netloc) > 255:
        raise ValidationError("url host too long")
    return True


def validate_alias(alias):
    """Validate an optional custom alias: 1-32 chars, alphanumeric plus - and _."""
    if alias is None:
        return True
    if not isinstance(alias, str) or not (1 <= len(alias) <= 32):
        raise ValidationError("alias must be 1-32 characters")
    if not all(c.isalnum() or c in "-_" for c in alias):
        raise ValidationError("alias contains invalid characters")
    return True
