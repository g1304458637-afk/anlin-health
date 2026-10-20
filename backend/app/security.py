import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
import jwt
from .config import settings


PBKDF_ITERATIONS = 600_000

WEAK_PASSWORD_FRAGMENTS = {
    "password", "123456", "qwerty", "abc123", "111111", "888888", "admin123",
    "letmein", "iloveyou", "sunshine", "welcome", "password1", "000000",
}


def validate_password_strength(password: str) -> str | None:
    """Return a human-readable rejection reason, or None when acceptable."""
    if len(password) < 10 or len(password) > 128:
        return "密码长度必须为10到128个字符"
    lowered = password.lower()
    if any(fragment in lowered for fragment in WEAK_PASSWORD_FRAGMENTS):
        return "密码过于常见，请更换"
    classes = sum([
        any(c.isdigit() for c in password),
        any(c.islower() for c in password),
        any(c.isupper() for c in password),
        any(not c.isalnum() for c in password),
    ])
    if classes < 3:
        return "密码需包含数字、大小写字母或符号中的至少三类"
    return None


def hash_password(password: str) -> str:
    reason = validate_password_strength(password)
    if reason:
        raise ValueError(reason)
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


def _key() -> str:
    key = settings.secret_key
    if not key and settings.app_env != "production":
        key = "DEV_INSECURE_ONLY_REPLACE_BEFORE_DEPLOY"
    return key


def access_token(user_id: int, token_version: int = 0) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=max(5, settings.session_ttl_minutes)),
        "type": "access",
        "tv": token_version,
    }
    return jwt.encode(payload, _key(), algorithm="HS256")


def decode_token(token: str) -> tuple[int, int]:
    """Return (user_id, token_version); raises jwt.PyJWTError on any problem."""
    payload = jwt.decode(token, _key(), algorithms=["HS256"], options={"require": ["sub", "exp", "iat"]})
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Bad token type")
    return int(payload["sub"]), int(payload.get("tv", 0))


def new_invite_code() -> str:
    return secrets.token_urlsafe(12)
