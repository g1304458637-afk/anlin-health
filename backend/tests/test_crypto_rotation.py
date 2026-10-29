"""Key versioning and rotation: legacy readability, v2 writes, script re-encryption."""
import base64
import os
from types import SimpleNamespace

from sqlalchemy import text

from app import crypto
from app.models import User
from scripts.rotate_field_keys import rotate_all


def _key():
    return base64.b64encode(os.urandom(32)).decode()


def _settings(current, previous=()):
    return SimpleNamespace(field_encryption_key=current,
                           field_encryption_previous_keys=tuple(previous))


def test_single_key_deployments_still_write_legacy_v1(monkeypatch):
    monkeypatch.setattr(crypto, "settings", _settings(_key()))
    sealed = crypto.encrypt_value("13800001111")
    assert sealed.startswith("enc:v1:")
    assert crypto.decrypt_value(sealed) == "13800001111"


def test_rotation_switches_writes_to_versioned_format_and_keeps_old_rows_readable(monkeypatch):
    old, new = _key(), _key()
    monkeypatch.setattr(crypto, "settings", _settings(old))
    legacy = crypto.encrypt_value("2026-10-29 的待确认密钥")
    assert legacy.startswith("enc:v1:")

    # Operator moved the old key into PREVIOUS_KEYS and provisioned the new one.
    monkeypatch.setattr(crypto, "settings", _settings(new, previous=[old]))
    # Legacy rows (no embedded version) decrypt via the previous-key fallback.
    assert crypto.decrypt_value(legacy) == "2026-10-29 的待确认密钥"
    fresh = crypto.encrypt_value("新的待确认密钥")
    assert fresh.startswith("enc:v2:2:")
    assert crypto.decrypt_value(fresh) == "新的待确认密钥"
    # Rows already re-sealed to the current version decrypt without the old key.
    monkeypatch.setattr(crypto, "settings", _settings(new))
    assert crypto.decrypt_value(fresh) == "新的待确认密钥"


def test_legacy_rows_decrypt_regardless_of_key_order(monkeypatch):
    oldest, middle, current = _key(), _key(), _key()
    monkeypatch.setattr(crypto, "settings", _settings(oldest))
    original = crypto.encrypt_value("多轮轮换后仍可读")
    monkeypatch.setattr(crypto, "settings", _settings(current, previous=[oldest, middle]))
    assert crypto.decrypt_value(original) == "多轮轮换后仍可读"


def test_unknown_version_and_missing_keys_fail_closed(monkeypatch):
    current = _key()
    monkeypatch.setattr(crypto, "settings", _settings(current, previous=[_key()]))
    forged = "enc:v2:9:" + "A" * 16 + ":" + "A" * 44
    try:
        crypto.decrypt_value(forged)
        raise AssertionError("expected RuntimeError for unknown key version")
    except RuntimeError as exc:
        assert "does not match any configured key" in str(exc)
    monkeypatch.setattr(crypto, "settings", SimpleNamespace(field_encryption_key="",
                                                            field_encryption_previous_keys=()))
    try:
        crypto.decrypt_value("enc:v1:AAAAAAAAAAAAAAAAAAAAAA:AAAAAAAAAAAAAAAAAAAAAA")
        raise AssertionError("expected RuntimeError for missing key")
    except RuntimeError:
        pass


def test_rows_stay_readable_after_rotation_converges_and_old_key_is_retired(monkeypatch):
    old, new = _key(), _key()
    monkeypatch.setattr(crypto, "settings", _settings(new, previous=[old]))
    sealed = crypto.encrypt_value("退役后仍可读")
    assert sealed.startswith("enc:v2:2:")
    # Operator removes the old key from the environment after the script converges.
    monkeypatch.setattr(crypto, "settings", _settings(new))
    assert crypto.decrypt_value(sealed) == "退役后仍可读"


def test_rotate_script_reseals_every_encrypted_value(client, token, db, monkeypatch):
    old, new = _key(), _key()
    monkeypatch.setattr(crypto, "settings", _settings(old))
    phone = "13800007777"
    staff = token("staff")
    created = client.post("/api/community/residents", headers=staff, json={
        "name": "虚构轮换居民", "birth_year": 1955, "community_id": 1, "phone": phone,
        "consent_confirmed": True, "evidence_ref": "ROTATE-1"})
    assert created.status_code == 201, created.text
    resident_id = created.json()["id"]

    def raw_phone():
        return db.execute(text("SELECT phone FROM residents WHERE id=:id"), {"id": resident_id}).scalar_one()

    assert raw_phone().startswith("enc:v1:") and phone not in raw_phone()

    # Rotate: new key becomes current, old key moves to previous. Old v1 rows stay
    # readable through the fallback, and the script re-seals them to the new version.
    monkeypatch.setattr(crypto, "settings", _settings(new, previous=[old]))
    assert rotate_all(check_only=True)["residents.phone"] == 1
    assert rotate_all(check_only=False)["residents.phone"] == 1
    assert raw_phone().startswith("enc:v2:2:")
    assert crypto.decrypt_value(raw_phone()) == phone
    assert rotate_all(check_only=True)["residents.phone"] == 0  # converged
    # The app's ORM reads the phone transparently after rotation.
    from app.models import Resident
    resident = db.query(Resident).filter(Resident.id == resident_id).one()
    assert resident.phone == phone
    _ = User  # keep the import honest for the account fixture reference
