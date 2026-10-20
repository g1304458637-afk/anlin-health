import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
import jwt
from .config import settings


PBKDF_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("Password must be at least 10 chars")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF_ITERATIONS}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, rounds, salt_b64, digest_b64 = stored.split("$")
        if scheme != "pbkdf2_sha256" or not 300_000 <= int(rounds) <= 1_000_000:
            return False
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.urlsafe_b64decode(salt_b64), int(rounds))
        return hmac.compare_digest(calc, base64.urlsafe_b64decode(digest_b64))
    except (TypeError, ValueError, KeyError):
        return False


def access_token(user_id: int) -> str:
    key = settings.secret_key
    if not key and settings.app_env != "production":
        key = "DEV_INSECURE_ONLY_REPLACE_BEFORE_DEPLOY"
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user_id), "iat": now, "exp": now + timedelta(hours=8), "type": "access"}, key, algorithm="HS256")


def decode_token(token: str) -> int:
    key = settings.secret_key or "DEV_INSECURE_ONLY_REPLACE_BEFORE_DEPLOY"
    payload = jwt.decode(token, key, algorithms=["HS256"], options={"require": ["sub", "exp", "iat"]})
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Bad token type")
    return int(payload["sub"])


def new_invite_code() -> str:
    return secrets.token_urlsafe(12)
