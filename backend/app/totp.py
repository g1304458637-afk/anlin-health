"""Minimal RFC 6238 TOTP (SHA-1, 6 digits, 30s step) using only the standard library.

MFA is optional per deployment: accounts with a confirmed totp_secret must supply
a valid code at password login. Secrets are generated as base32; provisioning URI follows
the conventional otpauth:// format compatible with common authenticator apps.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time


STEP_SECONDS = 30
WINDOW_STEPS = 1  # accept the previous/next step to tolerate clock drift


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def provisioning_uri(secret: str, account: str, issuer: str = "社区健康管理") -> str:
    from urllib.parse import quote
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}"
            f"?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period={STEP_SECONDS}")


def _code_at(secret: str, counter: int) -> str:
    padding = "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(secret.upper() + padding)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{value:06d}"


def verify_totp(secret: str | None, code: str, at: float | None = None) -> bool:
    if not secret or not code or not code.strip().isdigit() or len(code.strip()) != 6:
        return False
    now = at if at is not None else time.time()
    counter = int(now // STEP_SECONDS)
    code = code.strip()
    return any(hmac.compare_digest(code, _code_at(secret, counter + delta))
               for delta in range(-WINDOW_STEPS, WINDOW_STEPS + 1))
