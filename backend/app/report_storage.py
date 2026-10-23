"""Opaque, private report blobs. The only accepted name is a server UUID."""
import os
import re
import uuid
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings
from .crypto import _load_key

_KEY = re.compile(r"^[0-9a-f]{32}$")
_ENCRYPTED = b"report:v1:e:"
_PLAIN = b"report:v1:p:"
_AAD = b"community-health:report:v1"


def new_storage_key() -> str:
    return uuid.uuid4().hex


def _path(key: str) -> Path:
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise ValueError("Invalid report storage key")
    root = Path(getattr(settings, "report_storage_dir", "./private_reports")).expanduser().resolve()
    # The app's public static tree must never contain report data.
    static = (Path(__file__).resolve().parents[1] / "static").resolve()
    if root == static or static in root.parents:
        raise RuntimeError("Report storage cannot be inside static assets")
    return root / key


def write_blob(key: str, data: bytes) -> None:
    path = _path(key)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    secret = _load_key()
    if secret:
        nonce = os.urandom(12)
        payload = _ENCRYPTED + nonce + AESGCM(secret).encrypt(nonce, data, _AAD)
    else:
        if settings.app_env == "production":
            raise RuntimeError("Production report storage requires FIELD_ENCRYPTION_KEY")
        payload = _PLAIN + data
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def read_blob(key: str) -> bytes:
    payload = _path(key).read_bytes()
    if payload.startswith(_ENCRYPTED):
        secret = _load_key()
        if secret is None:
            raise RuntimeError("Report encryption key is missing")
        nonce = payload[len(_ENCRYPTED):len(_ENCRYPTED) + 12]
        ciphertext = payload[len(_ENCRYPTED) + 12:]
        return AESGCM(secret).decrypt(nonce, ciphertext, _AAD)
    if payload.startswith(_PLAIN) and settings.app_env != "production":
        return payload[len(_PLAIN):]
    raise RuntimeError("Unknown or unencrypted report blob")


def delete_blob(key: str) -> None:
    _path(key).unlink(missing_ok=True)
