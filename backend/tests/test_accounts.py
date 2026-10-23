"""Web-only account provisioning: staff creates resident accounts, family self-registers."""
import pytest
from sqlalchemy import select

from app.models import User, Resident
from app.ratelimit import register_limiter, provision_limiter

WEAK = 'short1!'
STRONG = 'Str0ng!Passphrase-42'


@pytest.fixture(autouse=True)
def clean_limiters():
    register_limiter.reset()
    provision_limiter.reset()
    yield


def test_staff_provisions_resident_account(client, token, db):
    h = token('staff')
    r = client.post('/api/community/accounts', headers=h, json={
        'username': 'wang奶奶', 'display_name': '王秀兰（演示）', 'initial_password': STRONG})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body['role'] == 'resident' and body['community_id'] == 1
    assert 'password' not in body and 'password_hash' not in body
    # The account can log in and see an empty resident view.
    login = client.post('/api/auth/login', json={'username': 'wang奶奶', 'password': STRONG})
    assert login.status_code == 200
    auth = {'Authorization': 'Bearer ' + login.json()['access_token']}
    assert client.get('/api/me', headers=auth).json()['role'] == 'resident'
    assert client.get('/api/residents/me', headers=auth).status_code == 404  # no archive yet


def test_provision_boundaries(client, token):
    staff = token('staff')
    # Weak initial password rejected.
    assert client.post('/api/community/accounts', headers=staff, json={
        'username': 'weak-user', 'display_name': '弱口令', 'initial_password': WEAK}).status_code == 422
    # Duplicate username rejected.
    ok = client.post('/api/community/accounts', headers=staff, json={
        'username': 'user_dup', 'display_name': '第一次', 'initial_password': STRONG})
    assert ok.status_code == 201
    assert client.post('/api/community/accounts', headers=staff, json={
        'username': 'user_dup', 'display_name': '第二次', 'initial_password': STRONG}).status_code == 409
    # Bad username shape rejected.
    assert client.post('/api/community/accounts', headers=staff, json={
        'username': 'ab', 'display_name': '太短', 'initial_password': STRONG}).status_code == 422
    # Family cannot provision; admin has no community so cannot either.
    assert client.post('/api/community/accounts', headers=token('family'), json={
        'username': 'sneaky1', 'display_name': '越权', 'initial_password': STRONG}).status_code == 403
    assert client.post('/api/community/accounts', headers=token('admin'), json={
        'username': 'adminmake1', 'display_name': '管理员', 'initial_password': STRONG}).status_code == 403


def test_provisioned_resident_full_flow(client, token):
    h = token('staff')
    acc = client.post('/api/community/accounts', headers=h, json={
        'username': 'li爷爷', 'display_name': '李大爷（演示）', 'initial_password': STRONG}).json()
    login = client.post('/api/auth/login', json={'username': 'li爷爷', 'password': STRONG})
    auth = {'Authorization': 'Bearer ' + login.json()['access_token']}
    # Self-archive with consent, then record a vital.
    r = client.post('/api/residents/me', headers=auth, json={
        'name': '李建国（虚构）', 'birth_year': 1953, 'community_id': 1, 'consent_to_community_care': True})
    assert r.status_code == 201, r.text
    rid = r.json()['id']
    assert client.post(f'/api/residents/{rid}/vitals', headers=auth,
                       json={'type': 'blood_pressure', 'systolic': 128, 'diastolic': 82}).status_code == 201
    # Staff of the same community can now see the resident (consent granted).
    assert client.get(f'/api/residents/{rid}', headers=h).json()['name'] == '李建国（虚构）'


def test_family_self_registration(client, token):
    from app.ratelimit import register_limiter
    register_limiter.reset()
    r = client.post('/api/auth/register-family', json={
        'username': 'daughter01', 'display_name': '王小丽', 'password': STRONG})
    assert r.status_code == 201, r.text
    data = r.json()
    assert data['user']['role'] == 'family'
    assert 'password_hash' not in data['user']
    auth = {'Authorization': 'Bearer ' + data['access_token']}
    # Family account starts with zero access and cannot escalate.
    assert client.get('/api/family/shared-residents', headers=auth).json() == []
    assert client.post('/api/residents/me', headers=auth, json={
        'name': '越权档案', 'birth_year': 1980, 'community_id': 1, 'consent_to_community_care': True}).status_code == 403
    assert client.post('/api/me/family-mode', headers=auth).status_code == 403
    # Family can be granted access through the normal invite-code flow.
    fam_code = data['user']['invite_code']
    grant = client.post('/api/residents/1/family-grants', headers=token('resident'),
                        json={'invite_code': fam_code, 'can_profile': True, 'can_vitals': True})
    assert grant.status_code == 201
    assert client.get('/api/residents/1', headers=auth).status_code == 200
    # Duplicates and weak passwords are rejected.
    assert client.post('/api/auth/register-family', json={
        'username': 'daughter01', 'display_name': '重复', 'password': STRONG}).status_code == 409
    assert client.post('/api/auth/register-family', json={
        'username': 'weakpw01', 'display_name': '弱口令', 'password': WEAK}).status_code == 422


def test_family_registration_rate_limited(client):
    register_limiter.reset()
    for i in range(10):
        client.post('/api/auth/register-family', json={
            'username': f'rate_lim_{i:02d}', 'display_name': '限流测试', 'password': STRONG})
    r = client.post('/api/auth/register-family', json={
        'username': 'rate_lim_over', 'display_name': '超限', 'password': STRONG})
    assert r.status_code == 429
