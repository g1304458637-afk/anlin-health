"""Application-level encryption for sensitive columns (AES-256-GCM).

Fields wrapped in `EncryptedString` are stored as `enc:v1:<nonce_b64>:<ciphertext_b64>`
when FIELD_ENCRYPTION_KEY is configured, and fall back to plaintext in development
when it is not. In production the key MUST be provisioned from a secret manager/KMS
and rotated per docs/SECURITY_PRIVACY.md; values encrypted under an old key are
re-encrypted on write, and history tables must be migrated by an operator script.
"""
import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import String, TypeDecorator

from .config import settings

_PREFIX = "enc:v1:"


def _load_key() -> bytes | None:
    raw = settings.field_encryption_key.strip()
    if not raw:
        return None
    try:
        key = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("FIELD_ENCRYPTION_KEY must be base64 of 32 random bytes") from exc
    if len(key) != 32:
        raise RuntimeError("FIELD_ENCRYPTION_KEY must decode to exactly 32 bytes")
    return key


def encrypt_value(plaintext: str) -> str:
    key = _load_key()
    if key is None:
        return plaintext
    nonce = os.urandom(12)
    sealed = AESGCM(key).encrypt(nonce, plaintext.encode(), b"community-health:v1")
    return _PREFIX + base64.b64encode(nonce).decode() + ":" + base64.b64encode(sealed).decode()


def decrypt_value(stored: str) -> str:
    if not stored.startswith(_PREFIX):
        return stored
    key = _load_key()
    if key is None:
        raise RuntimeError("Encrypted field present but FIELD_ENCRYPTION_KEY is not configured")
    nonce_b64, sealed_b64 = stored[len(_PREFIX):].split(":", 1)
    plain = AESGCM(key).decrypt(base64.b64decode(nonce_b64), base64.b64decode(sealed_b64), b"community-health:v1")
    return plain.decode()


class EncryptedString(TypeDecorator):
    """String column type that transparently encrypts values at rest when a key is set."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        return encrypt_value(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        return decrypt_value(value)
