"""Data lifecycle: soft-deleted vitals stay auditable; privacy deletion for reports."""
import json
from types import SimpleNamespace

from app import report_processing
from app.models import AuditLog, Vital
from app.report_models import MedicalReport


def _create_vital(client, token, **overrides):
    body = {"type": "blood_pressure", "systolic": 120, "diastolic": 78,
            "measured_at": "2026-10-29T08:30:00+08:00", "client_request_id": "lifecycle-1"}
    body.update(overrides)
    return client.post("/api/residents/1/vitals", headers=token("resident"), json=body)


def test_vital_delete_is_soft_and_audited_with_values(client, token, db):
    created = _create_vital(client, token)
    assert created.status_code == 201, created.text
    vital_id = created.json()["id"]
    assert client.delete(f"/api/residents/1/vitals/{vital_id}", headers=token("resident")).status_code == 204
    row = db.get(Vital, vital_id)
    assert row is not None and row.deleted_at is not None  # physically retained
    assert row.client_request_id is None
    # Every view hides it, and it counts as gone.
    assert client.get("/api/residents/1/vitals", headers=token("resident")).json() == []
    series = client.get("/api/residents/1/vitals/series",
                        params={"metric": "blood_pressure", "start_date": "2026-10-01",
                                "end_date": "2026-10-29"}, headers=token("resident")).json()
    assert series["count"] == 0
    stats = client.get("/api/community/health-stats", headers=token("staff")).json()
    assert stats["totals"]["blood_pressure"] == 0
    # The audit trail answers "who removed which reading".
    entry = db.query(AuditLog).filter(AuditLog.action == "delete_vital").one()
    detail = json.loads(entry.detail_json)
    assert detail["systolic"] == 120 and detail["diastolic"] == 78
    assert detail["client_request_id"] == "lifecycle-1"
    # Deleting again is a 404, not a double-audit.
    assert client.delete(f"/api/residents/1/vitals/{vital_id}", headers=token("resident")).status_code == 404


def test_deleted_vital_frees_idempotency_key_for_resubmission(client, token, db):
    first = _create_vital(client, token)
    assert client.delete(f"/api/residents/1/vitals/{first.json()['id']}",
                         headers=token("resident")).status_code == 204
    # The key was released with the delete, so re-submitting the same entry
    # (e.g. correcting a mistake) simply creates a new live record.
    resubmitted = _create_vital(client, token)
    assert resubmitted.status_code == 201
    assert resubmitted.json()["id"] != first.json()["id"]
    assert db.query(Vital).count() == 2  # one retained soft-deleted row + the new entry
    assert client.get("/api/residents/1/vitals", headers=token("resident")).json() != []


def test_report_deletion_removes_content_and_allows_reupload(client, token, db, monkeypatch, tmp_path):
    from app import report_storage
    monkeypatch.setattr(report_storage, "settings", SimpleNamespace(report_storage_dir=str(tmp_path), app_env="test"))
    monkeypatch.setattr(report_processing, "inspect_document",
                        lambda data: {"mime_type": "application/pdf", "page_count": 1})
    upload = client.post("/api/residents/1/reports?filename=legacy.pdf", content=b"to be deleted",
                         headers={**token("resident"), "Content-Type": "application/pdf"})
    assert upload.status_code == 201, upload.text
    report_id = upload.json()["id"]
    storage_key = db.get(MedicalReport, report_id).storage_key
    assert (tmp_path / storage_key).exists()

    # Staff with consent may read reports but must never destroy them.
    assert client.delete(f"/api/reports/{report_id}", headers=token("staff")).status_code == 403
    assert client.delete(f"/api/reports/{report_id}", headers=token("resident")).status_code == 204
    assert client.get(f"/api/reports/{report_id}", headers=token("resident")).status_code == 404
    assert not (tmp_path / storage_key).exists()
    assert db.query(MedicalReport).filter(MedicalReport.id == report_id).count() == 0
    entry = db.query(AuditLog).filter(AuditLog.action == "delete_report").one()
    detail = json.loads(entry.detail_json)
    assert detail["filename"] == "legacy.pdf" and "reviewed" not in detail

    # The unique (resident, sha256) slot is free; the same file can be uploaded again.
    reupload = client.post("/api/residents/1/reports?filename=legacy.pdf", content=b"to be deleted",
                           headers={**token("resident"), "Content-Type": "application/pdf"})
    assert reupload.status_code == 201
    assert db.query(MedicalReport).count() == 1


def test_report_deletion_blocked_while_processing(client, token, db, monkeypatch, tmp_path):
    from app import report_storage
    monkeypatch.setattr(report_storage, "settings", SimpleNamespace(report_storage_dir=str(tmp_path), app_env="test"))
    monkeypatch.setattr(report_processing, "inspect_document",
                        lambda data: {"mime_type": "application/pdf", "page_count": 1})
    upload = client.post("/api/residents/1/reports?filename=wip.pdf", content=b"working",
                         headers={**token("resident"), "Content-Type": "application/pdf"})
    report_id = upload.json()["id"]
    report = db.get(MedicalReport, report_id)
    report.status = "processing"
    db.commit()
    assert client.delete(f"/api/reports/{report_id}", headers=token("resident")).status_code == 409


def test_admin_summary_stays_aggregate_after_consent_queries_change(client, token):
    summary = client.get("/api/admin/summary", headers=token("admin"))
    assert summary.status_code == 200
    body = summary.json()
    assert body["community_count"] == 2
    assert all("active_resident_count" in row for row in body["communities"])
    assert sum(row["active_resident_count"] for row in body["communities"]) == 2
