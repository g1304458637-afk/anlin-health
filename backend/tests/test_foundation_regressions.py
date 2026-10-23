"""Synthetic regressions for parser portability, claims, and complete visit queues."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import subprocess
import sys

import pytest
from sqlalchemy import select

from app import report_processing
from app.models import ClaimRequest, ConsentEvent, Resident, User, Visit
from app.ratelimit import claim_limiter


def test_parser_resource_limits_are_platform_specific(monkeypatch):
    calls = []
    resource = SimpleNamespace(RLIMIT_CPU=1, RLIMIT_AS=2,
                               setrlimit=lambda *args: calls.append(args))
    monkeypatch.setitem(sys.modules, "resource", resource)
    monkeypatch.setattr(report_processing.sys, "platform", "win32")
    report_processing._apply_resource_limits()
    assert calls == []
    monkeypatch.setattr(report_processing.sys, "platform", "linux")
    report_processing._apply_resource_limits()
    assert calls == [(1, (12, 12)), (2, (768 * 1024 * 1024,) * 2)]


def test_parser_environment_failure_is_not_reported_as_a_bad_document(monkeypatch):
    def unavailable():
        raise report_processing.ParserEnvironmentError("解析环境无法启用资源限制")
    monkeypatch.setattr(report_processing, "_apply_resource_limits", unavailable)
    assert report_processing._child_result() == {
        "error": "解析环境无法启用资源限制", "error_type": "environment"}
    def cannot_start(*args, **kwargs):
        raise OSError("synthetic startup failure")
    monkeypatch.setattr(report_processing.subprocess, "run", cannot_start)
    with pytest.raises(report_processing.ParserEnvironmentError, match="环境无法启动"):
        report_processing.inspect_document(b"synthetic")


def test_parser_retains_subprocess_timeout(monkeypatch):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 18
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(report_processing.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="解析超时"):
        report_processing.inspect_document(b"synthetic")


def _pending_claim(client, token, db):
    claim_limiter.reset()
    staff, claimant = token("staff"), token("unlinked")
    created = client.post("/api/community/residents", headers=staff, json={
        "name": "虚构认领回归", "birth_year": 1954, "phone": "13800009999",
        "community_id": 1, "consent_confirmed": True, "evidence_ref": "SYNTHETIC-CLAIM"})
    assert created.status_code == 201
    resident_id = created.json()["id"]
    submitted = client.post("/api/residents/claim-requests", headers=claimant, json={
        "resident_id": resident_id, "full_name": "虚构认领回归", "phone": "13800009999"})
    assert submitted.status_code == 202
    claim = db.scalar(select(ClaimRequest).where(ClaimRequest.resident_id == resident_id))
    return staff, claimant, resident_id, claim.id


def test_pending_claim_prevents_family_role_switch_until_rejected(client, token, db):
    staff, claimant, resident_id, claim_id = _pending_claim(client, token, db)
    assert client.post("/api/me/family-mode", headers=claimant).status_code == 409
    assert client.post(f"/api/community/claim-requests/{claim_id}/review", headers=staff,
                       json={"approve": False}).status_code == 200
    switched = client.post("/api/me/family-mode", headers=claimant)
    assert switched.status_code == 200 and switched.json()["role"] == "family"
    db.expire_all()
    assert db.get(Resident, resident_id).owner_user_id is None


@pytest.mark.parametrize("changed", ["family", "inactive"])
def test_claim_approval_rechecks_requester_current_state(client, token, db, changed):
    staff, claimant, resident_id, claim_id = _pending_claim(client, token, db)
    requester = db.scalar(select(User).where(User.username == "unlinked"))
    if changed == "family":
        requester.role = "family"  # Simulates an older database or concurrent account change.
    else:
        requester.is_active = False
    db.commit()
    response = client.post(f"/api/community/claim-requests/{claim_id}/review",
                           headers=staff, json={"approve": True})
    assert response.status_code == 409
    db.expire_all()
    assert db.get(Resident, resident_id).owner_user_id is None
    assert db.get(ClaimRequest, claim_id).status == "pending"
    # An invalid claimant can still be rejected to clear the review queue.
    assert client.post(f"/api/community/claim-requests/{claim_id}/review", headers=staff,
                       json={"approve": False}).status_code == 200


def test_claim_approval_does_not_overwrite_another_owner(client, token, db, monkeypatch):
    """Inject a competing ownership write after validation, before the CAS write."""
    from sqlalchemy import update
    from sqlalchemy.orm import Session
    from sqlalchemy.sql.dml import Update

    staff, claimant, resident_id, claim_id = _pending_claim(client, token, db)
    other = User(username="other_claimant", display_name="虚构竞争申请人", role="resident",
                 invite_code="synthetic-other-claimant")
    db.add(other)
    db.commit()
    other_id = other.id
    execute = Session.execute
    injected = []

    def racing_execute(session, statement, *args, **kwargs):
        if isinstance(statement, Update) and statement.table.name == "residents" and not injected:
            injected.append(True)
            # Use the same transaction to simulate the write visible at the CAS
            # boundary without a timing-sensitive SQLite writer-lock test.
            execute(session, update(Resident).where(Resident.id == resident_id)
                    .values(owner_user_id=other_id).execution_options(synchronize_session=False))
        return execute(session, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "execute", racing_execute)
    response = client.post(f"/api/community/claim-requests/{claim_id}/review",
                           headers=staff, json={"approve": True})
    assert injected and response.status_code == 409
    db.expire_all()
    assert db.get(ClaimRequest, claim_id).status == "pending"
    # Both the claim transition and injected write were rolled back atomically.
    assert db.get(Resident, resident_id).owner_user_id is None


def _visits(db, resident_id, staff_id, count, status, due):
    db.add_all([Visit(resident_id=resident_id, staff_id=staff_id, status=status, due_date=due)
                for _ in range(count)])


def test_visit_pending_filter_and_totals_do_not_lose_older_tasks(client, token, db):
    staff = token("staff")
    actor = db.scalar(select(User).where(User.username == "staff"))
    today = datetime.now(timezone.utc).date()
    _visits(db, 1, actor.id, 100, "done", today)
    _visits(db, 1, actor.id, 1, "planned", today - timedelta(days=30))
    db.commit()
    assert len(client.get("/api/community/visits", headers=staff).json()) == 100
    pending = client.get("/api/community/visits?status=planned&limit=10", headers=staff).json()
    assert len(pending) == 1 and pending[0]["status"] == "planned"
    assert client.get("/api/community/visits/stats", headers=staff).json() == {
        "total": 101, "planned": 1, "done": 100, "overdue": 1}
    first = client.get("/api/community/visits?status=done&limit=10", headers=staff).json()
    second = client.get("/api/community/visits?status=done&limit=10&offset=10", headers=staff).json()
    assert len(first) == len(second) == 10
    assert {v["id"] for v in first}.isdisjoint(v["id"] for v in second)


def test_visit_consent_filter_precedes_pagination_and_stats(client, token, db):
    staff = token("staff")
    actor = db.scalar(select(User).where(User.username == "staff"))
    today = datetime.now(timezone.utc).date()
    hidden = Resident(name="虚构撤回居民", birth_year=1950, community_id=1, created_by_id=actor.id)
    db.add(hidden)
    db.flush()
    db.add_all([
        ConsentEvent(resident_id=hidden.id, actor_id=actor.id, purpose="community_care",
                     method="paper_assisted", granted=True, review_status="approved"),
        ConsentEvent(resident_id=hidden.id, actor_id=actor.id, purpose="community_care",
                     method="paper_assisted", granted=False, review_status="approved")])
    _visits(db, hidden.id, actor.id, 100, "planned", today)
    _visits(db, 2, actor.id, 3, "planned", today)  # Another community.
    _visits(db, 1, actor.id, 1, "planned", today - timedelta(days=1))
    db.commit()
    rows = client.get("/api/community/visits?limit=1", headers=staff).json()
    assert len(rows) == 1 and rows[0]["resident_id"] == 1
    assert client.get("/api/community/visits/stats", headers=staff).json() == {
        "total": 1, "planned": 1, "done": 0, "overdue": 1}
    for username in ("resident", "family", "admin"):
        assert client.get("/api/community/visits/stats", headers=token(username)).status_code == 403
