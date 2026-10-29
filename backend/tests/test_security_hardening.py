"""Hardening regressions: token signing, TOTP replay, upload throttling, login timing."""
import time
from types import SimpleNamespace

import jwt
import pytest

from app import report_processing, security, totp


PASSWORD = "DemoPass2026!"


def _code(secret, counter=None):
    return totp._code_at(secret, counter if counter is not None else int(time.time() // 30))


def test_missing_secret_key_no_longer_falls_back_to_a_public_constant(monkeypatch):
    from app.security import _key, _ephemeral_key  # noqa: F401 - presence documents the mechanism
    monkeypatch.setattr(security, "settings", SimpleNamespace(secret_key="", app_env="development"))
    key = security._key()
    assert key != "DEV_INSECURE_ONLY_REPLACE_BEFORE_DEPLOY"
    assert security._key() == key  # stable within the process, so sessions survive a request
    monkeypatch.setattr(security, "settings", SimpleNamespace(secret_key="", app_env="production"))
    with pytest.raises(RuntimeError):
        security._key()


def test_tokens_signed_with_the_old_dev_constant_are_rejected(monkeypatch):
    monkeypatch.setattr(security, "settings", SimpleNamespace(secret_key="", app_env="development"))
    now = int(time.time())
    legacy = jwt.encode({"sub": "1", "iat": now, "exp": now + 60, "type": "access", "tv": 0},
                        "DEV_INSECURE_ONLY_REPLACE_BEFORE_DEPLOY", algorithm="HS256")
    with pytest.raises(jwt.PyJWTError):
        security.decode_token(legacy)


def test_totp_code_cannot_be_replayed_for_a_second_login(client, token, db, monkeypatch):
    from app.models import User

    class _Clock:
        value = time.time()
    monkeypatch.setattr(totp, "_now", lambda: _Clock.value)

    h = token("staff")
    staged = client.post("/api/auth/mfa/totp", headers=h, json={"current_password": PASSWORD})
    assert staged.status_code == 200
    secret = staged.json()["secret"]
    assert client.post("/api/auth/mfa/totp/confirm", headers=h,
                       json={"current_password": PASSWORD, "totp_code": _code(secret)}).status_code == 200
    _Clock.value += totp.STEP_SECONDS + 1
    counter = int(_Clock.value // totp.STEP_SECONDS)
    code = _code(secret, counter)
    first = client.post("/api/auth/login/totp", json={"username": "staff", "password": PASSWORD, "totp_code": code})
    assert first.status_code == 200, first.text
    replay = client.post("/api/auth/login/totp", json={"username": "staff", "password": PASSWORD, "totp_code": code})
    assert replay.status_code == 401
    _Clock.value += totp.STEP_SECONDS + 1
    next_code = _code(secret, int(_Clock.value // totp.STEP_SECONDS))
    again = client.post("/api/auth/login/totp", json={"username": "staff", "password": PASSWORD, "totp_code": next_code})
    assert again.status_code == 200
    db.expire_all()
    user = db.query(User).filter(User.username == "staff").one()
    assert user.totp_last_used_counter == int(_Clock.value // totp.STEP_SECONDS)


def test_totp_confirmation_code_cannot_be_reused_after_disable(client, token, monkeypatch):
    class _Clock:
        value = time.time()
    monkeypatch.setattr(totp, "_now", lambda: _Clock.value)
    h = token("staff")
    staged = client.post("/api/auth/mfa/totp", headers=h, json={"current_password": PASSWORD})
    secret = staged.json()["secret"]
    code = _code(secret)
    assert client.post("/api/auth/mfa/totp/confirm", headers=h,
                       json={"current_password": PASSWORD, "totp_code": code}).status_code == 200
    replayed_disable = client.request("DELETE", "/api/auth/mfa/totp", headers=h,
                                      json={"current_password": PASSWORD, "totp_code": code})
    assert replayed_disable.status_code == 401


def test_report_uploads_are_throttled_per_user(client, token, monkeypatch, tmp_path):
    from app import report_storage
    from app.routes.reports import _upload_limiter
    _upload_limiter.reset()  # other tests upload as the same resident within the window
    monkeypatch.setattr(report_storage, "settings", SimpleNamespace(report_storage_dir=str(tmp_path), app_env="test"))
    monkeypatch.setattr(report_processing, "inspect_document",
                        lambda data: {"mime_type": "application/pdf", "page_count": 1})
    monkeypatch.setenv("REPORT_UPLOAD_RATE_PER_HOUR", "1")
    first = client.post("/api/residents/1/reports?filename=a.pdf", content=b"one",
                        headers={**token("resident"), "Content-Type": "application/pdf"})
    assert first.status_code == 201, first.text
    second = client.post("/api/residents/1/reports?filename=b.pdf", content=b"two",
                         headers={**token("resident"), "Content-Type": "application/pdf"})
    assert second.status_code == 429


def test_unknown_username_and_wrong_password_behave_identically(client):
    unknown = client.post("/api/auth/login", json={"username": "no_such_user", "password": PASSWORD})
    wrong = client.post("/api/auth/login", json={"username": "resident", "password": "DefinitelyWrong123!"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()
