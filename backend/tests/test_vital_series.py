"""Synthetic trend windows, provenance/privacy, and safe idempotent submission."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, FamilyGrant, User, Vital
from app.report_models import MedicalReport, ReportVitalLink
from app.routes import vitals


UTC = timezone.utc


def _actor(db, username="resident"):
    return db.scalar(select(User).where(User.username == username))


def _point(db, when, *, resident_id=1, metric="weight", **values):
    row = Vital(resident_id=resident_id, actor_id=_actor(db).id, type=metric,
                measured_at=when, **({"weight_kg": 60} if metric == "weight" and not values else values))
    db.add(row)
    db.flush()
    return row


def _series(client, headers, *, resident_id=1, metric="weight", start="2026-02-02", end=None, tz="Asia/Shanghai"):
    return client.get(f"/api/residents/{resident_id}/vitals/series", headers=headers, params={
        "metric": metric, "start_date": start, "end_date": end or start, "tz": tz})


def _payload(**changes):
    return {"type": "weight", "weight_kg": 62.5,
            "measured_at": (datetime.now(UTC) - timedelta(days=2)).replace(microsecond=0).isoformat(),
            "client_request_id": "synthetic-request-001", **changes}


def test_series_includes_all_points_and_both_blood_pressure_values(client, token, db):
    start = datetime(2026, 2, 2, 0, tzinfo=UTC)
    expected = []
    for i in range(125):
        expected.append(_point(db, start + timedelta(minutes=i), metric="blood_pressure",
                               systolic=110 + i % 20, diastolic=70 + i % 10).id)
    _point(db, start, metric="weight", weight_kg=72)
    db.commit()
    response = _series(client, token("resident"), metric="blood_pressure")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 125 and [p["id"] for p in data["points"]] == expected
    assert all(p["systolic"] is not None and p["diastolic"] is not None for p in data["points"])
    assert data["points"][-1]["systolic"] == 114 and data["points"][-1]["diastolic"] == 74
    assert all(p["source"] == "manual" and p["report_id"] is None for p in data["points"])
    assert all(datetime.fromisoformat(p["measured_at"]).utcoffset() == timedelta(0) for p in data["points"])


def test_series_respects_inclusive_local_dates_and_exclusive_next_midnight(client, token, db):
    start = datetime(2026, 2, 1, 16, tzinfo=UTC)  # Shanghai 2 February, midnight.
    end = start + timedelta(days=1)
    excluded_before = _point(db, start - timedelta(microseconds=1)).id
    included = [_point(db, start).id, _point(db, end - timedelta(microseconds=1)).id]
    excluded_after = _point(db, end).id
    db.commit()
    data = _series(client, token("resident")).json()
    assert data["timezone"] == "Asia/Shanghai"
    assert datetime.fromisoformat(data["window_start"]) == start
    assert datetime.fromisoformat(data["window_end"]) == end
    assert [p["id"] for p in data["points"]] == included
    assert not {excluded_before, excluded_after}.intersection(p["id"] for p in data["points"])


def test_series_handles_a_23_hour_day_in_a_dst_timezone(client, token, db):
    start = datetime(2026, 3, 8, 5, tzinfo=UTC)
    end = datetime(2026, 3, 9, 4, tzinfo=UTC)
    _point(db, start - timedelta(seconds=1))
    expected = [_point(db, start).id, _point(db, end - timedelta(seconds=1)).id]
    _point(db, end)
    db.commit()
    response = _series(client, token("resident"), start="2026-03-08", tz="America/New_York")
    assert response.status_code == 200
    data = response.json()
    assert [p["id"] for p in data["points"]] == expected
    assert datetime.fromisoformat(data["window_end"]) - datetime.fromisoformat(data["window_start"]) == timedelta(hours=23)


@pytest.mark.parametrize("params", [
    {"start": "2026-02-03", "end": "2026-02-02"},
    {"start": "2025-01-01", "end": "2026-01-02"},
    {"tz": "Not/A_Real_Zone"}, {"metric": "unknown"},
    {"start": "not-a-date"},
])
def test_series_rejects_invalid_ranges_and_parameters(client, token, params):
    assert _series(client, token("resident"), **params).status_code == 422


def test_series_allows_366_days_and_an_empty_result(client, token):
    response = _series(client, token("resident"), start="2025-01-01", end="2026-01-01")
    assert response.status_code == 200
    assert response.json()["points"] == [] and response.json()["count"] == 0


def test_series_rejects_unrepresentable_date_boundaries(client, token):
    # Invalid windows must be rejected, not overflow to a server error.
    assert _series(client, token("resident"), start="9999-12-31", tz="UTC").status_code == 422
    assert _series(client, token("resident"), start="0001-01-01").status_code == 422


def test_series_refuses_to_silently_truncate_above_5000_points(client, token, db):
    actor = _actor(db)
    when = datetime(2026, 2, 2, tzinfo=UTC)
    db.add_all([Vital(resident_id=1, actor_id=actor.id, type="weight", weight_kg=60,
                      measured_at=when) for _ in range(5001)])
    db.commit()
    headers = token("resident")
    response = _series(client, headers)
    assert response.status_code == 422 and "5000" in response.text
    last = db.scalar(select(Vital).order_by(Vital.id.desc()).limit(1))
    db.delete(last)
    db.commit()
    full = _series(client, headers)
    assert full.status_code == 200 and full.json()["count"] == 5000
    assert len(full.json()["points"]) == 5000


def test_series_family_scope_revocation_and_report_provenance(client, token, db):
    owner, family = _actor(db), _actor(db, "family")
    row = _point(db, datetime(2026, 2, 2, tzinfo=UTC))
    report = MedicalReport(resident_id=1, uploader_id=owner.id, storage_key="a" * 32,
                           sha256="b" * 64, mime_type="application/pdf", filename="synthetic.pdf",
                           page_count=1, status="confirmed")
    db.add(report)
    db.flush()
    db.add(ReportVitalLink(report_id=report.id, item_id="p1-i1", vital_id=row.id, needs_review=True))
    grant = FamilyGrant(resident_id=1, grantee_id=family.id, can_profile=True, can_vitals=False,
                        expires_at=datetime.now(UTC) + timedelta(days=1))
    db.add(grant)
    db.commit()
    family_headers = token("family")
    assert _series(client, family_headers).status_code == 404
    grant.can_profile, grant.can_vitals = False, True
    db.commit()
    response = _series(client, family_headers)
    assert response.status_code == 200
    point = response.json()["points"][0]
    assert point["source"] == "report"
    assert "report_id" not in point
    owner_point = _series(client, token("resident")).json()["points"][0]
    assert owner_point["report_id"] == report.id and owner_point["source_needs_review"] is True
    grant.revoked_at = datetime.now(UTC)
    db.commit()
    assert _series(client, family_headers).status_code == 404


def test_series_rechecks_staff_consent_and_cross_community_access(client, token):
    for username in ("staff2", "admin", "unlinked"):
        assert _series(client, token(username)).status_code == 404
    staff, owner = token("staff"), token("resident")
    assert _series(client, staff).status_code == 200
    assert client.post("/api/residents/1/consents", headers=owner, json={"granted": False}).status_code == 201
    assert _series(client, staff).status_code == 404
    assert _series(client, owner).status_code == 200


def test_vital_idempotent_retry_preserves_one_record_and_audit(client, token, db):
    headers = token("resident")
    payload = _payload()
    first = client.post("/api/residents/1/vitals", headers=headers, json=payload)
    retry = client.post("/api/residents/1/vitals", headers=headers, json=payload)
    assert first.status_code == retry.status_code == 201
    assert first.json()["id"] == retry.json()["id"]
    assert db.scalar(select(func.count()).select_from(Vital)) == 1
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "create_vital")) == 1
    assert client.post("/api/residents/1/vitals", headers=headers,
                       json={**payload, "weight_kg": 63}).status_code == 409
    # Request IDs are scoped to actor and resident, not global across the community.
    other = client.post("/api/residents/1/vitals", headers=token("staff"), json=payload)
    assert other.status_code == 201 and other.json()["id"] != first.json()["id"]


def test_idempotency_compares_context_and_explicit_timestamp(client, token):
    headers = token("resident")
    payload = _payload(type="blood_glucose", weight_kg=None, glucose_mmol_l=5.6, measurement_context="fasting")
    assert client.post("/api/residents/1/vitals", headers=headers, json=payload).status_code == 201
    for changed in ({"measurement_context": "after_meal"}, {"glucose_mmol_l": 5.7},
                    {"measured_at": (datetime.fromisoformat(payload["measured_at"]) - timedelta(minutes=1)).isoformat()}):
        assert client.post("/api/residents/1/vitals", headers=headers, json={**payload, **changed}).status_code == 409


def test_idempotency_without_client_timestamp_uses_the_first_server_timestamp(client, token):
    payload = _payload()
    payload.pop("measured_at")
    headers = token("resident")
    first = client.post("/api/residents/1/vitals", headers=headers, json=payload)
    retry = client.post("/api/residents/1/vitals", headers=headers, json=payload)
    assert first.status_code == retry.status_code == 201
    assert first.json()["id"] == retry.json()["id"]
    assert datetime.fromisoformat(first.json()["measured_at"]).replace(tzinfo=None) == datetime.fromisoformat(retry.json()["measured_at"]).replace(tzinfo=None)


def test_vital_normalizes_offsets_before_storage_and_equivalent_retry(client, token, db):
    headers = token("resident")
    instant = (datetime.now(UTC) - timedelta(days=3)).replace(hour=16, minute=0, second=0, microsecond=0)
    local = instant.astimezone(timezone(timedelta(hours=8)))
    payload = _payload(measured_at=local.isoformat())
    first = client.post("/api/residents/1/vitals", headers=headers, json=payload)
    assert first.status_code == 201
    stored = db.get(Vital, first.json()["id"])
    assert stored.measured_at.replace(tzinfo=UTC) == instant
    retried = client.post("/api/residents/1/vitals", headers=headers,
                          json={**payload, "measured_at": instant.isoformat()})
    assert retried.status_code == 201 and retried.json()["id"] == first.json()["id"]
    window = _series(client, headers, start=local.date().isoformat()).json()
    assert [p["id"] for p in window["points"]] == [first.json()["id"]]
    assert datetime.fromisoformat(window["points"][0]["measured_at"]) == instant


@pytest.mark.parametrize("changes", [
    {"measured_at": "2026-02-02T10:00:00"},
    {"measured_at": "2099-01-01T00:00:00Z"},
    {"measured_at": "2000-01-01T00:00:00Z"},
    {"measurement_context": "fasting"},  # Weight has no meal context.
    {"measurement_context": "invented"},
    {"client_request_id": "short"},
    {"client_request_id": "invalid / request"},
])
def test_vital_rejects_invalid_time_context_and_request_id(client, token, changes):
    response = client.post("/api/residents/1/vitals", headers=token("resident"), json=_payload(**changes))
    assert response.status_code == 422


def test_replay_does_not_bypass_timezone_validation(client, token):
    headers = token("resident")
    payload = _payload()
    assert client.post("/api/residents/1/vitals", headers=headers, json=payload).status_code == 201
    naive = datetime.fromisoformat(payload["measured_at"]).replace(tzinfo=None).isoformat()
    assert client.post("/api/residents/1/vitals", headers=headers,
                       json={**payload, "measured_at": naive}).status_code == 422


@pytest.mark.parametrize("changed", [False, True])
def test_idempotent_insert_race_rechecks_the_winner_without_duplicate_audit(client, token, db, monkeypatch, changed):
    """Simulate another request winning after the first lookup, using the real unique constraint."""
    headers = token("resident")
    payload = _payload()
    winner = client.post("/api/residents/1/vitals", headers=headers, json=payload)
    assert winner.status_code == 201
    actual_replay = vitals._replay
    calls = []
    def miss_once(*args, **kwargs):
        calls.append(True)
        return None if len(calls) == 1 else actual_replay(*args, **kwargs)
    monkeypatch.setattr(vitals, "_replay", miss_once)
    response = client.post("/api/residents/1/vitals", headers=headers,
                           json={**payload, **({"weight_kg": 70} if changed else {})})
    assert len(calls) == 2
    assert response.status_code == (409 if changed else 201)
    if not changed:
        assert response.json()["id"] == winner.json()["id"]
    assert db.scalar(select(func.count()).select_from(Vital)) == 1
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "create_vital")) == 1


def test_replay_still_requires_current_write_access(client, token):
    staff, owner = token("staff"), token("resident")
    payload = _payload()
    assert client.post("/api/residents/1/vitals", headers=staff, json=payload).status_code == 201
    assert client.post("/api/residents/1/consents", headers=owner, json={"granted": False}).status_code == 201
    assert client.post("/api/residents/1/vitals", headers=staff, json=payload).status_code == 404
