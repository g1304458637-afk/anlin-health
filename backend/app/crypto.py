"""Application-level encryption for sensitive columns (AES-256-GCM).

Formats stored in encrypted columns:
- `enc:v1:<nonce_b64>:<ciphertext_b64>` — the original single-key format; still
  written while only FIELD_ENCRYPTION_KEY is configured, and always decryptable
  by trying the current key and then FIELD_ENCRYPTION_PREVIOUS_KEYS in order.
- `enc:v2:<version>:<nonce_b64>:<ciphertext_b64>` — versioned format written
  once rotation has begun. Version N refers to FIELD_ENCRYPTION_PREVIOUS_KEYS[N-1]
  (oldest first); the current key's version is len(PREVIOUS_KEYS)+1.

Rotation procedure (see scripts/rotate_field_keys.py): move the old key into
FIELD_ENCRYPTION_PREVIOUS_KEYS, generate a new FIELD_ENCRYPTION_KEY, run the
script to re-encrypt every value to the current version, and only then remove
the old key from the environment. Without the script run, old values remain
readable — decryption falls back through the previous keys.
"""
import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import String, TypeDecorator

from .config import settings

_V1_PREFIX = "enc:v1:"
_AAD = b"community-health:v1"


def _decode_key(raw: str) -> bytes:
    try:
        key = base64.b64decode(raw.strip(), validate=True)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("FIELD_ENCRYPTION_KEY must be base64 of 32 random bytes") from exc
    if len(key) != 32:
        raise RuntimeError("FIELD_ENCRYPTION_KEY must decode to exactly 32 bytes")
    return key


def _load_key() -> bytes | None:
    raw = settings.field_encryption_key.strip()
    if not raw:
        return None
    return _decode_key(raw)


def _previous_keys() -> list[bytes]:
    raw = getattr(settings, "field_encryption_previous_keys", ()) or ()
    return [_decode_key(item) for item in raw if item and item.strip()]


def _seal(key: bytes, nonce: bytes, plaintext: bytes) -> bytes:
    return AESGCM(key).encrypt(nonce, plaintext, _AAD)


def _open(key: bytes, nonce: bytes, sealed: bytes) -> bytes:
    return AESGCM(key).decrypt(nonce, sealed, _AAD)


def current_key_version() -> int:
    """Version number new writes use: len(previous)+1, or 1 for the legacy format."""
    return len(_previous_keys()) + 1


def is_current_format(stored: str) -> bool:
    """True when `stored` was sealed by the current key version (nonces are random,
    so ciphertexts cannot be compared)."""
    version = current_key_version()
    if version == 1:
        return stored.startswith(_V1_PREFIX)
    return stored.startswith(f"enc:v2:{version}:")


def encrypt_value(plaintext: str) -> str:
    key = _load_key()
    if key is None:
        return plaintext
    previous = _previous_keys()
    version = len(previous) + 1
    nonce = os.urandom(12)
    sealed = _seal(key, nonce, plaintext.encode())
    body = base64.b64encode(nonce).decode() + ":" + base64.b64encode(sealed).decode()
    if version == 1:
        return _V1_PREFIX + body  # keep the original format for single-key deployments
    return f"enc:v2:{version}:" + body


def decrypt_value(stored: str) -> str:
    if not stored.startswith("enc:"):
        return stored
    try:
        return _decrypt_sealed(stored)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError("Encrypted field is malformed") from exc


def _decrypt_sealed(stored: str) -> str:
    if stored.startswith(_V1_PREFIX):
        key = _load_key()
        if key is None:
            raise RuntimeError("Encrypted field present but FIELD_ENCRYPTION_KEY is not configured")
        nonce_b64, sealed_b64 = stored[len(_V1_PREFIX):].split(":", 1)
        nonce, sealed = base64.b64decode(nonce_b64), base64.b64decode(sealed_b64)
        # Legacy rows carry no version; try the current key first, then any
        # previous keys, so decryption works before and after a rotation.
        try:
            return _open(key, nonce, sealed).decode()
        except InvalidTag:
            for candidate in _previous_keys():
                try:
                    return _open(candidate, nonce, sealed).decode()
                except InvalidTag:
                    continue
        raise RuntimeError("Encrypted field does not match any configured key")
    if stored.startswith("enc:v2:"):
        version_part, rest = stored[len("enc:v2:"):].split(":", 1)
        previous = _previous_keys()
        key = _load_key()
        if key is None and not previous:
            raise RuntimeError("Encrypted field present but FIELD_ENCRYPTION_KEY is not configured")
        try:
            version = int(version_part)
        except ValueError:
            raise RuntimeError("Encrypted field has an invalid key version") from None
        # The exact key first (version N = PREVIOUS_KEYS[N-1]; the current key's
        # version is len(PREVIOUS_KEYS)+1), then the current key, then every
        # previous key — wrong keys fail GCM authentication harmlessly. The
        # fallback keeps rows readable after a rotation converges and the old
        # keys are retired from the environment.
        candidates: list[bytes] = []
        if 1 <= version <= len(previous):
            candidates.append(previous[version - 1])
        if key is not None and key not in candidates:
            candidates.append(key)
        candidates.extend(k for k in previous if k not in candidates)
        nonce_b64, sealed_b64 = rest.split(":", 1)
        nonce, sealed = base64.b64decode(nonce_b64), base64.b64decode(sealed_b64)
        for candidate in candidates:
            try:
                return _open(candidate, nonce, sealed).decode()
            except InvalidTag:
                continue
        raise RuntimeError("Encrypted field does not match any configured key")
    raise RuntimeError("Encrypted field has an unknown format")


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
