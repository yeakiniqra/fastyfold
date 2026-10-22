"""Signed session cookie, CSRF tokens and password hashing."""

import hashlib
import hmac
import secrets

from itsdangerous import BadSignature, URLSafeTimedSerializer

SESSION_COOKIE = "ff_session"
SESSION_MAX_AGE = 14 * 24 * 3600


class Signer:
    def __init__(self, secret_key: str):
        if not secret_key:
            raise ValueError("fastyfold needs a non-empty secret_key")
        self._secret = secret_key.encode()
        self._serializer = URLSafeTimedSerializer(secret_key, salt="fastyfold-session")

    def dump(self, user: str) -> str:
        return self._serializer.dumps({"u": user, "n": secrets.token_hex(8)})

    def load(self, cookie: str | None) -> str | None:
        if not cookie:
            return None
        try:
            return self._serializer.loads(cookie, max_age=SESSION_MAX_AGE)["u"]
        except (BadSignature, KeyError, TypeError):
            return None

    def csrf(self, cookie: str | None) -> str:
        """Token bound to the session cookie; changes on every sign-in."""
        return hmac.new(self._secret, (cookie or "").encode(), hashlib.sha256).hexdigest()

    def csrf_ok(self, cookie: str | None, token: str | None) -> bool:
        return bool(cookie and token) and hmac.compare_digest(self.csrf(cookie), token)


def hash_password(password: str, *, iterations: int = 600_000) -> str:
    """PBKDF2-SHA256, stored as `pbkdf2$iterations$salt$hash`."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations).hex()
    return f"pbkdf2${iterations}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    return hmac.compare_digest(check, digest)
