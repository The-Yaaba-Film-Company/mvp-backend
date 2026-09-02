import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()

CSRF_ALGORITHM = "sha256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, ValueError, TypeError):
        return False


def csrf_token_value(csrf_secret: str, session_id: UUID) -> str:
    return hmac.new(csrf_secret.encode(), session_id.bytes, CSRF_ALGORITHM).hexdigest()


def verify_csrf(csrf_secret: str, session_id: UUID, token: str) -> bool:
    return hmac.compare_digest(csrf_token_value(csrf_secret, session_id), token)


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def session_expiry(idle_minutes: int) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=idle_minutes)