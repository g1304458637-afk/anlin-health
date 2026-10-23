"""Report privacy, explicit cloud permission, and one-time reviewed imports."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from app.models import ConsentEvent, User, Vital
from app.report_models import MedicalReport, ReportVitalLink
from app import report_processing
from sqlalchemy import select


def _upload(client, token, monkeypatch, username="resident", payload=b"synthetic report"):
    monkeypatch.setattr(report_processing, "inspect_document",
                        lambda data: {"mime_type": "application/pdf", "page_count": 1})
    return client.post("/api/residents/1/reports?filename=test.pdf", content=payload,
                       headers={**token(username), "Content-Type": "application/pdf"})


def test_upload_is_private_and_deduplicated(client, token, db, monkeypatch, tmp_path):
    from app import report_storage
    monkeypatch.setattr(report_storage, "settings", SimpleNamespace(report_storage_dir=str(tmp_path), app_env="test"))
    first = _upload(client, token, monkeypatch)
    assert first.status_code == 201, first.text
    report_id = first.json()["id"]
    assert first.json()["status"] == "uploaded"
    assert "storage_key" not in first.json() and "sha256" not in first.json()
    assert first.headers["cache-control"] == "no-store"
    second = _upload(client, token, monkeypatch)
    assert second.status_code == 201 and second.json()["id"] == report_id
    assert db.query(MedicalReport).count() == 1
    assert client.get(f"/api/reports/{report_id}", headers=token("family")).status_code == 404
    assert client.get(f"/api/reports/{report_id}/original", headers=token("admin")).status_code == 404
    assert client.get(f"/api/reports/{report_id}", headers=token("staff2")).status_code == 404
    assert client.get(f"/api/reports/{report_id}", headers=token("staff")).headers["cache-control"] == "no-store"
    family = db.scalar(select(User).where(User.username == "family"))
    grant = client.post("/api/residents/1/family-grants", json={"invite_code": family.invite_code,
                        "can_vitals": True}, headers=token("resident"))
    assert grant.status_code == 201
    assert client.get("/api/residents/1/vitals", headers=token("family")).status_code == 200
    assert client.get(f"/api/reports/{report_id}", headers=token("family")).status_code == 404
    got = client.get(f"/api/reports/{report_id}/original", headers=token("resident"))
    assert got.status_code == 200 and got.content == b"synthetic report"
    assert got.headers["cache-control"] == "no-store"


def test_upload_validation_and_consent_revocation(client, token, db, monkeypatch, tmp_path):
    from app import report_storage
    monkeypatch.setattr(report_storage, "settings", SimpleNamespace(report_storage_dir=str(tmp_path), app_env="test"))
    assert client.post("/api/residents/1/reports?filename=x.txt", content=b"abc",
                       headers={**token("resident"), "Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/residents/2/reports?filename=x.pdf", content=b"abc",
                       headers={**token("staff"), "Content-Type": "application/pdf"}).status_code == 404
    assert client.post("/api/residents/1/reports?filename=x.pdf", content=b"x" * (10 * 1024 * 1024 + 1),
                       headers={**token("resident"), "Content-Type": "application/pdf"}).status_code == 413
    first = _upload(client, token, monkeypatch)
    report_id = first.json()["id"]
    assert client.get(f"/api/reports/{report_id}", headers=token("staff")).status_code == 200
    owner = db.scalar(select(User).where(User.username == "resident"))
    db.add(ConsentEvent(resident_id=1, actor_id=owner.id, purpose="community_care",
                        granted=False, method="resident_self", review_status="approved"))
    db.commit()
    assert client.get(f"/api/reports/{report_id}", headers=token("staff")).status_code == 404
    assert client.get(f"/api/reports/{report_id}", headers=token("resident")).status_code == 200


def test_extraction_needs_explicit_cloud_permission(client, token, db, monkeypatch, tmp_path):
    from app import report_storage
    monkeypatch.setattr(report_storage, "settings", SimpleNamespace(report_storage_dir=str(tmp_path), app_env="test"))
    report_id = _upload(client, token, monkeypatch).json()["id"]
    url = f"/api/reports/{report_id}/extract"
    assert client.post(url, json={"allow_cloud": False}, headers=token("resident")).status_code == 422
    assert db.get(MedicalReport, report_id).status == "uploaded"
    queued = client.post(url, json={"allow_cloud": True}, headers=token("resident"))
    assert queued.status_code == 202 and queued.json()["status"] == "queued"
    assert client.post(url, json={"allow_cloud": True}, headers=token("resident")).status_code == 202
    assert db.get(MedicalReport, report_id).attempts == 0


def test_review_imports_once_and_preserves_candidate(client, token, db, monkeypatch, tmp_path):
    from app import report_storage
    monkeypatch.setattr(report_storage, "settings", SimpleNamespace(report_storage_dir=str(tmp_path), app_env="test"))
    report_id = _upload(client, token, monkeypatch).json()["id"]
    report = db.get(MedicalReport, report_id)
    candidate = {"title": "检验报告", "patient_name": "虚构王一", "items": [
        {"id": "glu1", "name": "葡萄糖", "value": "5.6", "unit": "mmol/L",
         "reference_range": "3.9-6.1", "flag": "normal", "page": 1, "quote": "葡萄糖 5.6 mmol/L"}]}
    report.candidate_json = json.dumps(candidate, ensure_ascii=False)
    report.status = "draft"
    report.revision = 1
    db.commit()
    review = {"revision": 1, "identity_confirmed": True, "title": "检验报告",
              "patient_name": "虚构王一", "specimen_collected_at": None, "reported_at": None,
              "items": candidate["items"], "imports": [{"item_id": "glu1", "type": "blood_glucose",
              "measured_at": datetime.now(timezone.utc).isoformat(), "glucose_mmol_l": 5.6}]}
    url = f"/api/reports/{report_id}/confirm"
    mismatch = {**review, "imports": [{**review["imports"][0], "glucose_mmol_l": 5.7}]}
    assert client.post(url, json=mismatch, headers=token("resident")).status_code == 422
    missing = {**review, "imports": [{**review["imports"][0], "glucose_mmol_l": None}]}
    assert client.post(url, json=missing, headers=token("resident")).status_code == 422
    duplicate_import = {**review, "imports": review["imports"] * 2}
    assert client.post(url, json=duplicate_import, headers=token("resident")).status_code == 422
    wrong_analyte = {**review, "items": [{**review["items"][0], "name": "尿糖"}]}
    assert client.post(url, json=wrong_analyte, headers=token("resident")).status_code == 422
    assert client.post(url, json={**review, "revision": 2}, headers=token("resident")).status_code == 409
    assert client.post(url, json={**review, "identity_confirmed": False},
                       headers=token("resident")).status_code == 422
    changed_quote = {**review, "items": [{**review["items"][0], "quote": "invented"}]}
    assert client.post(url, json=changed_quote, headers=token("resident")).status_code == 422
    good = client.post(url, json=review, headers=token("resident"))
    assert good.status_code == 200, good.text
    assert good.json()["status"] == "confirmed"
    assert good.json()["candidate"] == candidate
    assert len(good.json()["imported_vital_ids"]) == 1
    again = client.post(url, json=review, headers=token("resident"))
    assert again.status_code == 200
    assert db.query(Vital).count() == 1
    assert db.query(ReportVitalLink).count() == 1
    assert client.post(url, json={**review, "revision": 2}, headers=token("resident")).status_code == 409
    assert client.post(url, json={**review, "title": "changed after confirmation"},
                       headers=token("resident")).status_code == 409
    vital_id = good.json()["imported_vital_ids"][0]
    assert client.delete(f"/api/residents/1/vitals/{vital_id}", headers=token("resident")).status_code == 204
    after_delete = client.get(f"/api/reports/{report_id}", headers=token("resident")).json()
    assert after_delete["imported_vital_ids"] == []
    assert after_delete["deleted_import_count"] == 1
    assert client.post(url, json=review, headers=token("resident")).status_code == 200
    assert db.query(Vital).count() == 0
    assert db.query(ReportVitalLink).count() == 1


def test_original_integrity_check(client, token, db, monkeypatch, tmp_path):
    from app import report_storage
    monkeypatch.setattr(report_storage, "settings", SimpleNamespace(report_storage_dir=str(tmp_path), app_env="test"))
    report_id = _upload(client, token, monkeypatch).json()["id"]
    report = db.get(MedicalReport, report_id)
    (tmp_path / report.storage_key).write_bytes(b"report:v1:p:altered")
    got = client.get(f"/api/reports/{report_id}/original", headers=token("resident"))
    assert got.status_code == 409
    assert b"altered" not in got.content


def test_storage_encrypts_and_rejects_unsafe_keys(monkeypatch, tmp_path):
    from app import report_storage
    monkeypatch.setattr(report_storage, "settings", SimpleNamespace(report_storage_dir=str(tmp_path), app_env="test"))
    monkeypatch.setattr(report_storage, "_load_key", lambda: b"K" * 32)
    key = report_storage.new_storage_key()
    report_storage.write_blob(key, b"synthetic secret result")
    assert b"synthetic secret result" not in (tmp_path / key).read_bytes()
    assert report_storage.read_blob(key) == b"synthetic secret result"
    try:
        report_storage.read_blob("../escape")
    except ValueError:
        pass
    else:
        assert False, "unsafe storage key was accepted"


def test_corrupt_ciphertext_returns_no_document(client, token, db, monkeypatch, tmp_path):
    from app import report_storage
    monkeypatch.setattr(report_storage, "settings", SimpleNamespace(report_storage_dir=str(tmp_path), app_env="test"))
    monkeypatch.setattr(report_storage, "_load_key", lambda: b"K" * 32)
    report_id = _upload(client, token, monkeypatch).json()["id"]
    report = db.get(MedicalReport, report_id)
    path = tmp_path / report.storage_key
    sealed = path.read_bytes()
    path.write_bytes(sealed[:-1] + bytes([sealed[-1] ^ 1]))
    response = client.get(f"/api/reports/{report_id}/original", headers=token("resident"))
    assert response.status_code == 503
    assert "synthetic" not in response.text and "报告原件" in response.text
