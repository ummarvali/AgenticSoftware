"""Simple API-key based authentication / role authorization."""


def resolve_role(db, headers):
    """Return the role (READ/WRITE/ADMIN) for the given request headers, or None."""
    key = None
    auth_header = headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        key = auth_header[7:].strip()
    if not key:
        key = headers.get("X-Api-Key")
    if not key:
        return None
    with db.lock:
        row = db.conn.execute("SELECT role FROM api_keys WHERE key=?", (key,)).fetchone()
    return row["role"] if row else None


def role_allows(role, required):
    """Check whether `role` satisfies the `required` access level."""
    if required == "READ":
        return role in ("READ", "WRITE", "ADMIN")
    if required == "WRITE":
        return role in ("WRITE", "ADMIN")
    return False
