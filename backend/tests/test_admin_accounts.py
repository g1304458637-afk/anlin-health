"""Only admins can provision bounded, community-scoped staff accounts."""
import pytest
from sqlalchemy import select

from app.models import AuditLog, User
from app.routes.admin import create_staff_limiter


PASSWORD = "Str0ng!StaffPass-42"


@pytest.fixture(autouse=True)
def reset_create_limit():
    create_staff_limiter.reset()
    yield


def _body(**changes):
    return {"username": "newstaff01", "display_name": "新工作人员", "community_id": 1,
            "initial_password": PASSWORD, **changes}


def _login(client, username):
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def test_admin_creates_staff_only_and_audits_without_returning_secret(client, token, db):
    admin = token("admin")
    response = client.post("/api/admin/users", headers=admin, json=_body())
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["role"] == "staff" and payload["community_id"] == 1
    assert payload["is_active"] is True and payload["mfa_enabled"] is False
    assert "initial_password" not in payload and "password_hash" not in payload and "invite_code" not in payload
    assert client.get("/api/me", headers=_login(client, "newstaff01")).json()["role"] == "staff"
    db.expire_all()
    created = db.scalar(select(User).where(User.username == "newstaff01"))
    assert created is not None and created.community_id == 1
    assert db.scalar(select(AuditLog).where(AuditLog.action == "create_staff_account", AuditLog.entity_id == created.id))


def test_staff_creation_denies_nonadmin_and_role_injection(client, token, db):
    for username in ("staff", "staff2", "resident", "family"):
        assert client.post("/api/admin/users", headers=token(username), json=_body()).status_code == 403
    assert client.post("/api/admin/users", json=_body()).status_code == 401
    admin = token("admin")
    for injected in ({"role": "admin"}, {"is_active": False}, {"password_hash": "evil"}):
        assert client.post("/api/admin/users", headers=admin, json=_body(**injected)).status_code == 422
    assert db.scalar(select(User).where(User.username == "newstaff01")) is None


def test_staff_creation_validates_community_name_password_and_duplicates(client, token):
    admin = token("admin")
    assert client.post("/api/admin/users", headers=admin, json=_body(community_id=999)).status_code == 404
    assert client.post("/api/admin/users", headers=admin, json=_body(username="bad name")).status_code == 422
    assert client.post("/api/admin/users", headers=admin, json=_body(initial_password="weak")).status_code == 422
    assert client.post("/api/admin/users", headers=admin, json=_body()).status_code == 201
    assert client.post("/api/admin/users", headers=admin, json=_body()).status_code == 409
    assert client.post("/api/admin/users", headers=admin, json=_body(username="staff")).status_code == 409


def test_new_staff_is_community_bound_and_can_independently_review(client, token):
    admin = token("admin")
    staff = token("staff")
    cross = client.post("/api/admin/users", headers=admin, json=_body(username="crossstaff", community_id=2))
    assert cross.status_code == 201
    peer = client.post("/api/admin/users", headers=admin, json=_body(username="samepeer", community_id=1))
    assert peer.status_code == 201
    created = client.post("/api/community/residents", headers=staff, json={
        "name": "虚构待审档案", "birth_year": 1950, "community_id": 1,
        "phone": "13900000001", "consent_confirmed": True, "evidence_ref": "PAPER-SECOND-STAFF"})
    assert created.status_code == 201 and created.json()["consent_active"] is False
    event_id = client.get("/api/community/consent-reviews", headers=staff).json()[0]["id"]
    assert client.post(f"/api/community/consent-events/{event_id}/review", headers=staff,
                       json={"approve": True}).status_code == 403
    assert client.post(f"/api/community/consent-events/{event_id}/review", headers=_login(client, "crossstaff"),
                       json={"approve": True}).status_code == 404
    same = _login(client, "samepeer")
    assert client.post(f"/api/community/consent-events/{event_id}/review", headers=same,
                       json={"approve": True}).status_code == 200
    assert client.get(f"/api/residents/{created.json()['id']}", headers=same).status_code == 200
    assert client.get(f"/api/residents/{created.json()['id']}", headers=_login(client, "crossstaff")).status_code == 404


def test_admin_staff_creation_is_rate_limited_per_actor(client, token):
    admin = token("admin")
    invalid = _body(username="bad name")
    for _ in range(10):
        assert client.post("/api/admin/users", headers=admin, json=invalid).status_code == 422
    assert client.post("/api/admin/users", headers=admin, json=_body()).status_code == 429
