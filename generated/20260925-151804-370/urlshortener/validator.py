"""Input validation and sanitization for URLs and custom aliases."""
import ipaddress
import re
from urllib.parse import urlparse

RESERVED_ALIASES = {"api", "healthz", "metrics", "favicon.ico"}
ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
BLOCKED_PREFIXES = ("javascript:", "data:", "file:", "vbscript:")
BLOCKED_HOSTNAMES = {"localhost"}


def validate_url(url, max_length=2048):
    """Raise ValueError with a generic message if the URL is malformed or
    points at a disallowed/malicious target. Returns True if valid."""
    if not url or not isinstance(url, str):
        raise ValueError("long_url is required")
    if len(url) > max_length:
        raise ValueError("long_url exceeds maximum length")
    lowered = url.strip().lower()
    for prefix in BLOCKED_PREFIXES:
        if lowered.startswith(prefix):
            raise ValueError("URL scheme is not allowed")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http/https URLs are allowed")
    if not parsed.netloc:
        raise ValueError("URL must include a host")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL must include a host")
    if hostname.lower() in BLOCKED_HOSTNAMES:
        raise ValueError("URL host is not allowed")
    ip = None
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None
    if ip is not None and (
        ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast
    ):
        raise ValueError("URL host is not allowed")
    return True


def validate_alias(alias):
    if not ALIAS_RE.match(alias):
        raise ValueError("custom_alias must be 3-32 alphanumeric/-/_ characters")
    if alias.lower() in RESERVED_ALIASES:
        raise ValueError("custom_alias is reserved")
    return True
