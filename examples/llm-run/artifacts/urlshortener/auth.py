"""API-key based authentication with hashed secrets."""
import hashlib
import time
import uuid


def hash_secret(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def generate_api_key():
    return uuid.uuid4().hex + uuid.uuid4().hex


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def register_user(db, username, password):
    if db.query("SELECT 1 FROM users WHERE username=?", (username,)):
        raise ValueError("username already taken")
    user_id = uuid.uuid4().hex
    now = _now()
    db.execute(
        "INSERT INTO users(user_id, username, password_hash, created_at) VALUES (?,?,?,?)",
        (user_id, username, hash_secret(password), now),
    )
    api_key = generate_api_key()
    db.execute(
        "INSERT INTO api_keys(key_id, user_id, key_hash, created_at, revoked) VALUES (?,?,?,?,0)",
        (uuid.uuid4().hex, user_id, hash_secret(api_key), now),
    )
    return user_id, api_key


def login_user(db, username, password):
    rows = db.query("SELECT * FROM users WHERE username=?", (username,))
    if not rows or rows[0]["password_hash"] != hash_secret(password):
        raise ValueError("invalid credentials")
    user_id = rows[0]["user_id"]
    api_key = generate_api_key()
    db.execute(
        "INSERT INTO api_keys(key_id, user_id, key_hash, created_at, revoked) VALUES (?,?,?,?,0)",
        (uuid.uuid4().hex, user_id, hash_secret(api_key), _now()),
    )
    return user_id, api_key


def authenticate(db, api_key):
    """Return the owning user_id for a valid, non-revoked API key, else None."""
    if not api_key:
        return None
    rows = db.query(
        "SELECT * FROM api_keys WHERE key_hash=? AND revoked=0", (hash_secret(api_key),)
    )
    return rows[0]["user_id"] if rows else None
