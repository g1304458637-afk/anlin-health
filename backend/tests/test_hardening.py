"""Regression checks for consent, account ownership, and MFA boundaries."""
import base64
import os
import time
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app import crypto, totp
from app.models import ConsentEvent, User
from app.ratelimit import claim_limiter, login_limiter


PASSWORD = "DemoPass2026!"


def _code(secret, counter=None):
    return totp._code_at(secret, counter if counter is not None else int(time.time() // totp.STEP_SECONDS))


def _bearer(token):
    return {"Authorization": "Bearer " + token}


def test_paper_consent_stays_closed_until_independent_review(client, token):
    submitter = token("staff")
    peer = token("staff_peer")
    created = client.post("/api/community/residents", headers=submitter, json={
        "name": "虚构纸质对象", "birth_year": 1950, "community_id": 1,
        "phone": "13800001234", "consent_confirmed": True, "evidence_ref": "PAPER-BOUNDARY-1"})
    assert created.status_code == 201
    rid = created.json()["id"]
    assert created.json()["consent_active"] is False
    assert client.get(f"/api/residents/{rid}", headers=submitter).status_code == 404
    assert rid not in {row["id"] for row in client.get("/api/community/residents", headers=submitter).json()}
    event_id = client.get("/api/community/consent-reviews", headers=peer).json()[0]["id"]
    assert client.post(f"/api/community/consent-events/{event_id}/review", headers=submitter,
                       json={"approve": True}).status_code == 403
    assert client.post(f"/api/community/consent-events/{event_id}/review", headers=peer,
                       json={"approve": True}).status_code == 200
    assert client.get(f"/api/residents/{rid}", headers=submitter).status_code == 200


def test_self_withdrawal_cannot_be_revived_by_pending_paper_event(client, token):
    owner = token("resident")
    staff = token("staff")
    assert client.post("/api/residents/1/consents", headers=owner, json={"granted": False}).status_code == 201
    pending = client.post("/api/residents/1/consents", headers=staff, json={
        "granted": True, "evidence_ref": "PAPER-AFTER-WITHDRAWAL"})
    assert pending.status_code == 201 and pending.json()["review_status"] == "pending"
    assert client.get("/api/residents/1", headers=staff).status_code == 404
    assert client.get("/api/residents/1", headers=owner).status_code == 200
    assert client.get("/api/community/stats", headers=staff).json()["resident_count"] == 0


def test_resident_patch_rejects_null_required_fields(client, token, db):
    h = token("resident")
    for field in ("name", "birth_year", "sex"):
        response = client.patch("/api/residents/1", headers=h, json={field: None})
        assert response.status_code == 422, (field, response.text)
    assert client.patch("/api/residents/1", headers=h, json={"phone": None}).status_code == 200
    db.expire_all()
    assert client.get("/api/residents/1", headers=h).json()["name"] == "虚构王一"


def test_mfa_requires_credentials_and_confirm_then_rotates_sessions(client, token, db, monkeypatch):
    login_limiter.reset()

    class _Clock:
        value = time.time()
    monkeypatch.setattr(totp, "_now", lambda: _Clock.value)

    def next_window():
        # A shown code is single-use now; move to the next 30s step for a fresh one.
        _Clock.value += totp.STEP_SECONDS + 1
        return int(_Clock.value // totp.STEP_SECONDS)

    initial = token("staff")
    assert client.post("/api/auth/mfa/totp", headers=initial, json={"current_password": "wrong"}).status_code == 401
    staged = client.post("/api/auth/mfa/totp", headers=initial, json={"current_password": PASSWORD})
    assert staged.status_code == 200
    first_secret = staged.json()["secret"]
    assert client.post("/api/auth/mfa/totp/confirm", headers=initial, json={
        "current_password": "wrong", "totp_code": _code(first_secret)}).status_code == 401
    assert client.post("/api/auth/mfa/totp/confirm", headers=initial, json={
        "current_password": PASSWORD, "totp_code": "abcdef"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "staff", "password": PASSWORD}).status_code == 200
    enabled = client.post("/api/auth/mfa/totp/confirm", headers=initial, json={
        "current_password": PASSWORD, "totp_code": _code(first_secret)})
    assert enabled.status_code == 200
    assert client.get("/api/me", headers=initial).status_code == 401
    current = _bearer(enabled.json()["access_token"])
    assert client.post("/api/auth/mfa/totp", headers=current, json={"current_password": PASSWORD}).status_code == 401
    assert client.post("/api/auth/mfa/totp", headers=current, json={
        "current_password": "wrong", "totp_code": _code(first_secret)}).status_code == 401
    # The code consumed by the confirmation cannot stage a rotation in the same window.
    assert client.post("/api/auth/mfa/totp", headers=current, json={
        "current_password": PASSWORD, "totp_code": _code(first_secret)}).status_code == 401
    rotation = client.post("/api/auth/mfa/totp", headers=current, json={
        "current_password": PASSWORD, "totp_code": _code(first_secret, next_window())})
    assert rotation.status_code == 200
    second_secret = rotation.json()["secret"]
    assert second_secret != first_secret
    db.expire_all()
    assert db.scalar(select(User).where(User.username == "staff")).totp_secret == first_secret
    assert client.request("DELETE", "/api/auth/mfa/totp", headers=current,
                          json={"current_password": PASSWORD, "totp_code": "abcdef"}).status_code == 401
    rotated = client.post("/api/auth/mfa/totp/confirm", headers=current, json={
        "current_password": PASSWORD, "totp_code": _code(second_secret, next_window())})
    assert rotated.status_code == 200
    assert client.get("/api/me", headers=current).status_code == 401
    current = _bearer(rotated.json()["access_token"])
    disabled = client.request("DELETE", "/api/auth/mfa/totp", headers=current,
                              json={"current_password": PASSWORD, "totp_code": _code(second_secret, next_window())})
    assert disabled.status_code == 200
    assert client.get("/api/me", headers=current).status_code == 401
    assert client.get("/api/me", headers=_bearer(disabled.json()["access_token"])).status_code == 200


def test_totp_login_failures_persist_in_account_lockout(client, token, db):
    login_limiter.reset()
    h = token("staff")
    staged = client.post("/api/auth/mfa/totp", headers=h, json={"current_password": PASSWORD})
    confirmed = client.post("/api/auth/mfa/totp/confirm", headers=h, json={
        "current_password": PASSWORD, "totp_code": _code(staged.json()["secret"])})
    assert confirmed.status_code == 200
    for attempt in range(5):
        bad = client.post("/api/auth/login/totp", json={
            "username": "staff", "password": PASSWORD, "totp_code": "abcdef"})
        assert bad.status_code == 401, (attempt, bad.text)
    good_code = _code(staged.json()["secret"])
    assert client.post("/api/auth/login/totp", json={
        "username": "staff", "password": PASSWORD, "totp_code": good_code}).status_code == 423
    db.expire_all()
    account = db.scalar(select(User).where(User.username == "staff"))
    assert account.failed_login_count >= 5 and account.locked_until is not None


def test_pending_factor_expires_and_bad_codes_lock_management(client, token, db):
    h = token("staff")
    staged = client.post("/api/auth/mfa/totp", headers=h, json={"current_password": PASSWORD})
    secret = staged.json()["secret"]
    account = db.scalar(select(User).where(User.username == "staff"))
    account.totp_pending_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert client.post("/api/auth/mfa/totp/confirm", headers=h, json={
        "current_password": PASSWORD, "totp_code": _code(secret)}).status_code == 409
    restaged = client.post("/api/auth/mfa/totp", headers=h, json={"current_password": PASSWORD})
    assert restaged.status_code == 200
    for _ in range(5):
        assert client.post("/api/auth/mfa/totp/confirm", headers=h, json={
            "current_password": PASSWORD, "totp_code": "abcdef"}).status_code == 401
    db.expire_all()
    account = db.scalar(select(User).where(User.username == "staff"))
    assert account.totp_pending_secret is None and account.locked_until is not None
    assert client.post("/api/auth/mfa/totp", headers=h, json={"current_password": PASSWORD}).status_code == 423


def test_sensitive_mfa_and_claim_fields_encrypt_with_legacy_readability(client, token, db, monkeypatch):
    claim_limiter.reset()
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setattr(crypto, "settings", SimpleNamespace(field_encryption_key=key))
    staff = token("staff")
    archived = client.post("/api/community/residents", headers=staff, json={
        "name": "虚构加密认领", "birth_year": 1952, "community_id": 1, "phone": "13700009999",
        "consent_confirmed": True, "evidence_ref": "PAPER-ENCRYPTED-1"})
    assert archived.status_code == 201
    rid = archived.json()["id"]
    claimant = token("unlinked")
    assert client.post("/api/residents/claim-requests", headers=claimant, json={
        "resident_id": rid, "full_name": "虚构加密认领", "phone": "13700009999"}).status_code == 202
    raw_claim = db.execute(text("SELECT stated_phone FROM claim_requests WHERE resident_id=:id"), {"id": rid}).scalar_one()
    assert raw_claim.startswith("enc:v1:") and "13700009999" not in raw_claim
    setup = client.post("/api/auth/mfa/totp", headers=staff, json={"current_password": PASSWORD})
    secret = setup.json()["secret"]
    raw_pending = db.execute(text("SELECT totp_pending_secret FROM users WHERE username='staff'")).scalar_one()
    assert raw_pending.startswith("enc:v1:") and secret not in raw_pending
    confirmed = client.post("/api/auth/mfa/totp/confirm", headers=staff, json={
        "current_password": PASSWORD, "totp_code": _code(secret)})
    assert confirmed.status_code == 200
    raw_active = db.execute(text("SELECT totp_secret FROM users WHERE username='staff'")).scalar_one()
    assert raw_active.startswith("enc:v1:") and secret not in raw_active
    assert crypto.decrypt_value("legacy-plaintext") == "legacy-plaintext"
