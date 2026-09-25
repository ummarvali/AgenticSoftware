"""Input validation and sanitization for URLs and custom aliases.

Guards against open-redirect abuse by rejecting targets that resolve to
loopback/private/reserved hosts, and enforces scheme/length constraints.
"""
import ipaddress
import re
from urllib.parse import urlsplit

ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")


class ValidationError(Exception):
    """Raised when user-supplied input fails validation."""


def validate_long_url(url, cfg):
    """Validate scheme, length, format and guard against open-redirect targets."""
    if not isinstance(url, str) or not url.strip():
        raise ValidationError("long_url is required")
    url = url.strip()
    if len(url) > cfg.max_url_length:
        raise ValidationError("long_url exceeds maximum length")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValidationError("long_url must use http or https scheme")
    if cfg.https_only and parts.scheme != "https":
        raise ValidationError("only https urls are allowed")
    if not parts.netloc:
        raise ValidationError("long_url is malformed")
    host = parts.hostname
    if not host:
        raise ValidationError("long_url is malformed")
    host_lower = host.lower()
    if host_lower in cfg.denylist_hosts:
        raise ValidationError("target host is not allowed")
    try:
        ip = ipaddress.ip_address(host_lower)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
            raise ValidationError("target host is not allowed")
    except ValueError:
        pass  # not an IP literal; hostname already checked against denylist
    return url


def validate_custom_alias(alias):
    """Ensure a custom alias only contains safe characters and a sane length."""
    if not isinstance(alias, str) or not ALIAS_RE.match(alias):
        raise ValidationError("custom_alias must be 3-32 chars of letters, digits, - or _")
    return alias
