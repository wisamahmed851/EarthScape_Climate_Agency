"""Password hashing (Argon2id), session tokens and CSRF comparison."""
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()
# Verified when the user does not exist so unknown and known usernames take similar time.
_DUMMY = _hasher.hash(secrets.token_hex(16))


def hash_password(password):
    return _hasher.hash(password)


def verify_password(stored_hash, password):
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def burn_time(password):
    verify_password(_DUMMY, password)


def new_token():
    return secrets.token_urlsafe(32)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def same(a, b):
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())
