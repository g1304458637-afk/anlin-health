"""Durable report extraction never bypasses current actor authorization."""
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest

from sqlalchemy import select, text

from app.db import SessionLocal
from app.models import ConsentEvent, User, Vital
from app.report_models import MedicalReport
from app import report_storage, report_worker, crypto


def _queued(db, monkeypatch, tmp_path):
    monkeypatch.setattr(report_storage, "settings", SimpleNamespace(report_storage_dir=str(tmp_path), app_env="test"))
    owner = db.scalar(select(User).where(User.username == "resident"))
    data = b"synthetic private report"
    key = report_storage.new_storage_key()
    report_storage.write_blob(key, data)
    report = MedicalReport(resident_id=1, uploader_id=owner.id, storage_key=key,
                           sha256=hashlib.sha256(data).hexdigest(), mime_type="application/pdf",
                           filename="synthetic.pdf", page_count=1, status="queued", revision=0,
                           attempts=0, requested_by_id=owner.id,
                           requested_token_version=owner.token_version)
    db.add(report)
    db.commit()
    return report.id


def _candidate():
    return {"title": "虚构报告", "patient_name": "虚构王一", "items": [{
        "id": "p1-i1", "name": "葡萄糖", "value": "5.6", "unit": "mmol/L",
        "reference_range": "3.9-6.1", "flag": "", "page": 1,
        "quote": "葡萄糖 5.6 mmol/L"}], "pages": [{"page": 1, "text": "葡萄糖 5.6 mmol/L"}],
        "warnings": [], "model": "synthetic", "extraction_method": "deepseek-text"}


def test_worker_claims_durable_queue_but_only_produces_draft(db, monkeypatch, tmp_path):
    key = b"R" * 32
    monkeypatch.setattr(crypto, "_load_key", lambda: key)
    monkeypatch.setattr(report_storage, "_load_key", lambda: key)
    report_id = _queued(db, monkeypatch, tmp_path)
    called = []

    async def fake_extract(data, authorize):
        await authorize()
        called.append(data)
        return _candidate()

    monkeypatch.setattr(report_worker, "extract_document", fake_extract)
    assert asyncio.run(report_worker.process_one()) is True
    with SessionLocal() as fresh:
        report = fresh.get(MedicalReport, report_id)
        assert report.status == "draft" and report.revision == 1 and report.attempts == 1, report.error
        assert report.confirmed_at is None and report.reviewed_json is None
        assert json.loads(report.candidate_json) == _candidate()
        stored = fresh.execute(text("SELECT candidate_json FROM medical_reports WHERE id=:id"),
                               {"id": report_id}).scalar_one()
        assert stored.startswith("enc:v1:") and "葡萄糖" not in stored
        assert fresh.query(Vital).count() == 0
    assert called == [b"synthetic private report"]


def test_revoked_consent_blocks_worker_before_provider(db, monkeypatch, tmp_path):
    report_id = _queued(db, monkeypatch, tmp_path)
    owner = db.scalar(select(User).where(User.username == "resident"))
    db.add(ConsentEvent(resident_id=1, actor_id=owner.id, purpose="community_care",
                        granted=False, method="resident_self", review_status="approved"))
    db.commit()
    # Owner's own authorization survives withdrawal; the staff actor is the one
    # whose previously queued cloud request must be stopped.
    staff = db.scalar(select(User).where(User.username == "staff"))
    report = db.get(MedicalReport, report_id)
    report.requested_by_id = staff.id
    report.requested_token_version = staff.token_version
    db.commit()
    calls = []

    async def forbidden_extract(data, authorize):
        calls.append(True)
        return _candidate()

    monkeypatch.setattr(report_worker, "extract_document", forbidden_extract)
    assert asyncio.run(report_worker.process_one()) is True
    with SessionLocal() as fresh:
        report = fresh.get(MedicalReport, report_id)
        assert report.status == "failed" and report.candidate_json is None
    assert calls == []


def test_consent_revoked_between_pages_blocks_next_send(db, monkeypatch, tmp_path):
    report_id = _queued(db, monkeypatch, tmp_path)
    staff = db.scalar(select(User).where(User.username == "staff"))
    owner = db.scalar(select(User).where(User.username == "resident"))
    report = db.get(MedicalReport, report_id)
    report.requested_by_id = staff.id
    report.requested_token_version = staff.token_version
    db.commit()
    sends_after_revoke = []

    async def extraction_with_revocation(data, authorize):
        await authorize()  # The first page would be authorized.
        with SessionLocal() as fresh:
            fresh.add(ConsentEvent(resident_id=1, actor_id=owner.id, purpose="community_care",
                                   granted=False, method="resident_self", review_status="approved"))
            fresh.commit()
        await authorize()  # Before the next page's API request.
        sends_after_revoke.append(True)
        return _candidate()

    monkeypatch.setattr(report_worker, "extract_document", extraction_with_revocation)
    assert asyncio.run(report_worker.process_one()) is True
    with SessionLocal() as fresh:
        report = fresh.get(MedicalReport, report_id)
        assert report.status == "failed" and report.candidate_json is None
    assert sends_after_revoke == []


def test_logout_token_version_blocks_queued_job(db, monkeypatch, tmp_path):
    report_id = _queued(db, monkeypatch, tmp_path)
    owner = db.scalar(select(User).where(User.username == "resident"))
    owner.token_version += 1
    db.commit()
    calls = []

    async def forbidden_extract(data, authorize):
        calls.append(True)
        return _candidate()

    monkeypatch.setattr(report_worker, "extract_document", forbidden_extract)
    assert asyncio.run(report_worker.process_one()) is True
    with SessionLocal() as fresh:
        report = fresh.get(MedicalReport, report_id)
        assert report.status == "failed" and report.candidate_json is None
    assert calls == []


def test_expired_lease_fails_without_automatic_resend(db, monkeypatch, tmp_path):
    report_id = _queued(db, monkeypatch, tmp_path)
    report = db.get(MedicalReport, report_id)
    report.status = "processing"
    report.attempts = 1
    report.lease_token = "expired-lease"
    report.lease_until = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    calls = []

    async def forbidden_extract(data, authorize):
        calls.append(True)
        return _candidate()

    monkeypatch.setattr(report_worker, "extract_document", forbidden_extract)
    assert asyncio.run(report_worker.process_one()) is False
    with SessionLocal() as fresh:
        report = fresh.get(MedicalReport, report_id)
        assert report.status == "failed" and report.attempts == 1 and report.lease_token is None
    assert calls == []


def test_expired_lease_cannot_authorize_provider_send(db, monkeypatch, tmp_path):
    report_id = _queued(db, monkeypatch, tmp_path)
    claimed = report_worker._claim()
    assert claimed and claimed[0] == report_id
    with SessionLocal() as fresh:
        report = fresh.get(MedicalReport, report_id)
        report.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        fresh.commit()
    with SessionLocal() as fresh, pytest.raises(ValueError, match="超时|任务状态"):
        report_worker._authorize(fresh, report_id, claimed[1])


def test_reclaimed_job_cannot_overwrite_new_state(db, monkeypatch, tmp_path):
    report_id = _queued(db, monkeypatch, tmp_path)
    original_authorize = report_worker._authorize
    calls = 0

    def authorize_then_reclaim(session, rid, lease):
        nonlocal calls
        calls += 1
        report, actor = original_authorize(session, rid, lease)
        if calls == 3:  # Initial read, provider pre-send, final validation.
            report.status = "failed"
            report.lease_token = None
            session.commit()
        return report, actor

    async def fake_extract(data, authorize):
        await authorize()
        return _candidate()

    monkeypatch.setattr(report_worker, "_authorize", authorize_then_reclaim)
    monkeypatch.setattr(report_worker, "extract_document", fake_extract)
    assert asyncio.run(report_worker.process_one()) is True
    with SessionLocal() as fresh:
        report = fresh.get(MedicalReport, report_id)
        assert report.status == "failed" and report.candidate_json is None
        assert report.revision == 0 and report.confirmed_at is None


def test_provider_failure_keeps_original_and_no_candidate(db, monkeypatch, tmp_path):
    report_id = _queued(db, monkeypatch, tmp_path)

    async def failing_extract(data, authorize):
        await authorize()
        raise ValueError("识别服务暂不可用")

    monkeypatch.setattr(report_worker, "extract_document", failing_extract)
    assert asyncio.run(report_worker.process_one()) is True
    with SessionLocal() as fresh:
        report = fresh.get(MedicalReport, report_id)
        assert report.status == "failed" and report.attempts == 1
        assert report.candidate_json is None and report.confirmed_at is None
        assert report_storage.read_blob(report.storage_key) == b"synthetic private report"
