"""Security-focused tests: brute-force defense, sessions, MFA, claiming, evidence review,
field encryption, production settings, migrations. All data fictional."""
import base64
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import User
from app.ratelimit import login_limiter, wechat_limiter, claim_limiter
from app.security import validate_password_strength, verify_password
from app import totp as totp_mod

STRONG = "Str0ng!Passphrase-42"


@pytest.fixture(autouse=True)
def clean_limiters():
    login_limiter.reset()
    wechat_limiter.reset()
    claim_limiter.reset()
    yield


def login(client, username, password="DemoPass2026!"):
    return client.post('/api/auth/login', json={'username': username, 'password': password})


# ---- password login: lockout and rate limit ------------------------------------

def test_login_endpoint_success(client, token):
    r = login(client, 'staff')
    assert r.status_code == 200 and r.json()['user']['role'] == 'staff'
    # Works as the general replacement for dev-login on the console.
    me = client.get('/api/me', headers={'Authorization': 'Bearer ' + r.json()['access_token']})
    assert me.json()['username'] == 'staff'


def test_failed_logins_lock_account(client, db):
    for _ in range(5):
        assert login(client, 'staff', 'wrong-pass').status_code == 401
    # Even the correct password is rejected while the account is locked.
    assert login(client, 'staff').status_code == 423
    db.expire_all()  # the fixture identity map caches insert-time state
    user = db.scalar(select(User).where(User.username == 'staff'))
    assert user.failed_login_count >= 5 and user.locked_until is not None
    # Expiry clears the lock.
    user.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert login(client, 'staff').status_code == 200
    db.expire_all()
    assert db.scalar(select(User).where(User.username == 'staff')).failed_login_count == 0


def test_login_rate_limit_per_ip_username(client):
    for _ in range(10):
        login(client, 'admin', 'nope')
    assert login(client, 'admin', 'nope').status_code == 429
    assert login(client, 'admin', 'DemoPass2026!').status_code == 429


def test_deactivated_account_cannot_login(client, token, db):
    admin = token('admin')
    staff_id = db.scalar(select(User.id).where(User.username == 'staff'))
    assert client.post(f'/api/admin/users/{staff_id}/deactivate', headers=admin).status_code == 204
    assert login(client, 'staff').status_code == 403


# ---- password change & session invalidation ------------------------------------

def test_password_change_policy_and_session_invalidation(client, token):
    h = token('staff')
    assert client.post('/api/auth/change-password', headers=h,
                       json={'current_password': 'DemoPass2026!', 'new_password': 'short1!'}).status_code == 422
    assert client.post('/api/auth/change-password', headers=h,
                       json={'current_password': 'DemoPass2026!', 'new_password': 'password123!'}).status_code == 422
    assert client.post('/api/auth/change-password', headers=h,
                       json={'current_password': 'wrong', 'new_password': STRONG}).status_code == 401
    ok = client.post('/api/auth/change-password', headers=h,
                     json={'current_password': 'DemoPass2026!', 'new_password': STRONG})
    assert ok.status_code == 200
    # The old bearer token is dead immediately.
    assert client.get('/api/me', headers=h).status_code == 401
    new_token = ok.json()['access_token']
    assert client.get('/api/me', headers={'Authorization': 'Bearer ' + new_token}).json()['username'] == 'staff'
    # New password works at the login endpoint; old one does not.
    assert login(client, 'staff').status_code == 401
    assert login(client, 'staff', STRONG).status_code == 200


def test_logout_all_invalidates_existing_token(client, token):
    h = token('resident')
    assert client.get('/api/me', headers=h).status_code == 200
    assert client.post('/api/auth/logout-all', headers=h).status_code == 200
    assert client.get('/api/me', headers=h).status_code == 401


# ---- TOTP second factor ---------------------------------------------------------

def test_totp_flow(client, token, monkeypatch):
    class _Clock:
        value = time.time()
    monkeypatch.setattr(totp_mod, "_now", lambda: _Clock.value)
    h = token('staff')
    setup = client.post('/api/auth/mfa/totp', headers=h, json={'current_password': 'DemoPass2026!'})
    assert setup.status_code == 200
    secret = setup.json()['secret']
    assert setup.json()['uri'].startswith('otpauth://totp/')
    # Provisioning does not activate the factor until the staged code is proven.
    assert login(client, 'staff').status_code == 200
    code = totp_mod._code_at(secret, int(_Clock.value // 30))
    confirmed = client.post('/api/auth/mfa/totp/confirm', headers=h,
                            json={'current_password': 'DemoPass2026!', 'totp_code': code})
    assert confirmed.status_code == 200
    assert client.get('/api/me', headers=h).status_code == 401
    h = {'Authorization': 'Bearer ' + confirmed.json()['access_token']}
    # Password alone no longer logs in; the response flags MFA.
    r = login(client, 'staff')
    assert r.status_code == 401 and r.headers.get('X-MFA-Required') == 'totp'
    wrong = client.post('/api/auth/login/totp', json={'username': 'staff', 'password': 'DemoPass2026!', 'totp_code': '000000'})
    assert wrong.status_code == 401
    # The confirmation consumed that code; a login needs the next window's code.
    assert client.post('/api/auth/login/totp', json={'username': 'staff', 'password': 'DemoPass2026!', 'totp_code': code}).status_code == 401
    _Clock.value += totp_mod.STEP_SECONDS + 1
    login_code = totp_mod._code_at(secret, int(_Clock.value // 30))
    good = client.post('/api/auth/login/totp', json={'username': 'staff', 'password': 'DemoPass2026!', 'totp_code': login_code})
    assert good.status_code == 200
    assert client.get('/api/me', headers={'Authorization': 'Bearer ' + good.json()['access_token']}).json()['username'] == 'staff'
    # Disabling MFA rotates the token version and clears the factor.
    _Clock.value += totp_mod.STEP_SECONDS + 1
    disable_code = totp_mod._code_at(secret, int(_Clock.value // 30))
    assert client.request('DELETE', '/api/auth/mfa/totp', headers=h,
                          json={'current_password': 'DemoPass2026!', 'totp_code': disable_code}).status_code == 200
    assert login(client, 'staff').status_code == 200


def test_totp_rejects_garbage():
    assert not totp_mod.verify_totp(None, '123456')
    secret = totp_mod.new_totp_secret()
    code = totp_mod._code_at(secret, int(time.time() // 30))
    assert totp_mod.verify_totp(secret, code)
    assert not totp_mod.verify_totp(secret, 'abcdef')
    assert not totp_mod.verify_totp(secret, code + '1')


# ---- admin account lifecycle ----------------------------------------------------

def test_admin_user_management_boundaries(client, token, db):
    admin = token('admin')
    staff_h = token('staff')
    # Staff cannot use admin endpoints; admin list exposes no credentials or health data.
    assert client.get('/api/admin/users', headers=staff_h).status_code == 403
    users = client.get('/api/admin/users', headers=admin).json()
    assert users and all('password_hash' not in u and 'invite_code' not in u for u in users)
    ids = {u['username']: u['id'] for u in users}
    # Self-deactivation is blocked.
    assert client.post(f"/api/admin/users/{ids['admin']}/deactivate", headers=admin).status_code == 409
    # Deactivation kills the target's live session instantly.
    assert client.post(f"/api/admin/users/{ids['staff']}/deactivate", headers=admin).status_code == 204
    assert client.get('/api/me', headers=staff_h).status_code == 401
    assert login(client, 'staff').status_code == 403
    assert client.post(f"/api/admin/users/{ids['staff']}/activate", headers=admin).status_code == 204
    assert login(client, 'staff').status_code == 200


# ---- offline archive claiming ----------------------------------------------------

def _create_resident_with_phone(client, staff_headers, name="虚构认领对象", phone="13800001111"):
    r = client.post('/api/community/residents', headers=staff_headers, json={
        'name': name, 'birth_year': 1954, 'phone': phone, 'community_id': 1,
        'consent_confirmed': True, 'evidence_ref': 'PAPER-CLAIM-0001'})
    assert r.status_code == 201, r.text
    return r.json()['id']


def test_claim_flow_requires_staff_review(client, token):
    staff = token('staff')
    rid = _create_resident_with_phone(client, staff)
    h = token('unlinked')
    # A non-matching submission is accepted with the same generic answer but stores nothing.
    same = client.post('/api/residents/claim-requests', headers=h,
                       json={'resident_id': rid, 'full_name': '错误名字', 'phone': '13800001111'})
    match = client.post('/api/residents/claim-requests', headers=h,
                        json={'resident_id': rid, 'full_name': '虚构认领对象', 'phone': '13800001111'})
    assert same.status_code == match.status_code == 202
    assert same.json() == match.json()
    pending = client.get('/api/community/claim-requests', headers=staff).json()
    assert len(pending) == 1 and pending[0]['name_matched'] and pending[0]['phone_matched']
    claim_id = pending[0]['id']
    # Cross-community staff sees nothing.
    assert client.get('/api/community/claim-requests', headers=token('staff2')).json() == []
    assert client.post(f'/api/community/claim-requests/{claim_id}/review', headers=token('staff2'),
                       json={'approve': True}).status_code == 404
    # Approval transfers ownership; the requester immediately sees the archive.
    done = client.post(f'/api/community/claim-requests/{claim_id}/review', headers=staff, json={'approve': True})
    assert done.status_code == 200 and done.json()['status'] == 'approved'
    mine = client.get('/api/residents/me', headers=h).json()
    assert mine['id'] == rid
    # Re-review is blocked, and the granted claim does NOT create care consent automatically.
    assert client.post(f'/api/community/claim-requests/{claim_id}/review', headers=staff, json={'approve': True}).status_code == 409
    assert client.get('/api/residents/me', headers=token('staff2')).status_code == 404


def test_claim_request_rate_limited(client, token, db):
    h = token('unlinked')
    for _ in range(5):
        client.post('/api/residents/claim-requests', headers=h,
                    json={'resident_id': 99999, 'full_name': '虚构名字', 'phone': '13800000000'})
    assert client.post('/api/residents/claim-requests', headers=h,
                       json={'resident_id': 99999, 'full_name': '虚构名字', 'phone': '13800000000'}).status_code == 429


def test_claim_role_boundaries(client, token):
    staff = token('staff')
    rid = _create_resident_with_phone(client, staff, name="虚构双认领", phone="13900002222")
    # Only profile-less resident accounts may request a claim.
    first = token('unlinked')
    assert client.post('/api/residents/claim-requests', headers=first,
                       json={'resident_id': rid, 'full_name': '虚构双认领', 'phone': '13900002222'}).status_code == 202
    claim_id = client.get('/api/community/claim-requests', headers=staff).json()[0]['id']
    assert client.post(f'/api/community/claim-requests/{claim_id}/review', headers=staff, json={'approve': True}).status_code == 200
    # A resident who already owns an archive cannot claim another one.
    owned = token('resident')
    assert client.post('/api/residents/claim-requests', headers=owned,
                       json={'resident_id': rid, 'full_name': '虚构双认领', 'phone': '13900002222'}).status_code == 409
    # Family accounts cannot claim at all.
    family = token('family')
    assert client.post('/api/residents/claim-requests', headers=family,
                       json={'resident_id': rid, 'full_name': '虚构双认领', 'phone': '13900002222'}).status_code == 403


# ---- consent evidence review ------------------------------------------------------

def test_paper_consent_requires_review_and_rejection_cuts_access(client, token):
    staff = token('staff')
    r = client.post('/api/community/residents', headers=staff, json={
        'name': '虚构待复核', 'birth_year': 1948, 'community_id': 1,
        'consent_confirmed': True, 'evidence_ref': 'PAPER-REVIEW-0001'})
    rid = r.json()['id']
    # Pending paper consent never grants access and appears in the review queue.
    assert r.json()['consent_active'] is False
    assert client.get(f'/api/residents/{rid}', headers=staff).status_code == 404
    queue = client.get('/api/community/consent-reviews', headers=staff).json()
    assert any(e['evidence_ref'] == 'PAPER-REVIEW-0001' and e['review_status'] == 'pending' for e in queue)
    event_id = queue[0]['id']
    assert client.post(f'/api/community/consent-events/{event_id}/review', headers=token('staff2'),
                       json={'approve': True}).status_code == 404
    assert client.post(f'/api/community/consent-events/{event_id}/review', headers=staff,
                       json={'approve': True}).status_code == 403
    rejected = client.post(f'/api/community/consent-events/{event_id}/review', headers=token('staff_peer'),
                           json={'approve': False, 'note': '证据编号无法核对'})
    assert rejected.status_code == 200 and rejected.json()['review_status'] == 'rejected'
    # Rejection immediately revokes staff visibility, not the resident's own access.
    assert client.get(f'/api/residents/{rid}', headers=staff).status_code == 404
    visible = client.get('/api/community/residents', headers=staff).json()
    assert rid not in {r['id'] for r in visible}
    double = client.post(f'/api/community/consent-events/{event_id}/review', headers=staff, json={'approve': True})
    assert double.status_code == 409


def test_resident_self_consent_auto_approved(client, token):
    h = token('unlinked')
    r = client.post('/api/residents/me', headers=h, json={
        'name': '虚构自我同意', 'birth_year': 1960, 'community_id': 1, 'consent_to_community_care': True})
    assert r.status_code == 201
    rid = r.json()['id']
    consents = client.get(f'/api/residents/{rid}/consents', headers=h).json()
    assert consents[0]['review_status'] == 'approved'


# ---- family invite rotation and decline -------------------------------------------

def test_invite_code_rotation_invalidates_old_code(client, token, db):
    # The FAMILY member's invite code is what a resident redeems; rotation must kill the old one.
    old = client.get('/api/me', headers=token('family')).json()['invite_code']
    rotated = client.post('/api/me/invite-code/rotate', headers=token('family'))
    assert rotated.status_code == 200 and rotated.json()['invite_code'] != old
    new_code = rotated.json()['invite_code']
    assert client.post('/api/residents/1/family-grants', headers=token('resident'),
                       json={'invite_code': old}).status_code == 422
    ok = client.post('/api/residents/1/family-grants', headers=token('resident'), json={'invite_code': new_code})
    assert ok.status_code == 201
    grant_id = ok.json()['id']
    assert client.get('/api/residents/1/vitals', headers=token('family')).status_code == 200
    # The grantee can decline the invitation themselves.
    assert client.post(f'/api/residents/1/family-grants/{grant_id}/decline', headers=token('family')).status_code == 204
    assert client.get('/api/residents/1/vitals', headers=token('family')).status_code == 404


# ---- security headers and demo status ----------------------------------------------

def test_security_headers_present(client):
    r = client.get('/console')
    assert r.headers.get('X-Content-Type-Options') == 'nosniff'
    assert r.headers.get('X-Frame-Options') == 'DENY'
    assert r.headers.get('Referrer-Policy') == 'no-referrer'
    assert 'default-src' in (r.headers.get('Content-Security-Policy') or '')


def test_demo_status_reports_runtime_flag(client):
    assert client.get('/api/auth/demo-status').json()['enabled'] is True


# ---- password policy unit -----------------------------------------------------------

def test_password_policy_rules():
    assert validate_password_strength('short1!x') is not None
    assert validate_password_strength('password123!') is not None
    assert validate_password_strength('alllowercase123') is not None  # only 2 classes
    assert validate_password_strength(STRONG) is None
    assert validate_password_strength('完全中文密码不含数字') is not None


# ---- field encryption at rest --------------------------------------------------------

def test_phone_encrypted_at_rest(client, token, db, monkeypatch):
    from app import crypto as crypto_mod
    from types import SimpleNamespace
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setattr(crypto_mod, 'settings', SimpleNamespace(field_encryption_key=key))
    h = token('staff')
    r = client.post('/api/community/residents', headers=h, json={
        'name': '虚构加密对象', 'birth_year': 1951, 'phone': '13700003333', 'community_id': 1,
        'consent_confirmed': True, 'evidence_ref': 'PAPER-ENC-0001'})
    assert r.status_code == 201
    assert r.json()['phone'] == '13700003333'
    # Raw column value must be ciphertext, never the plaintext phone number.
    from sqlalchemy import text
    stored = db.execute(text("SELECT phone FROM residents WHERE name='虚构加密对象'")).scalar()
    assert stored.startswith('enc:v1:')
    assert '13700003333' not in stored
    # Legacy plaintext values remain readable when no prefix is present.
    from app.crypto import decrypt_value
    assert decrypt_value('plain-value') == 'plain-value'
