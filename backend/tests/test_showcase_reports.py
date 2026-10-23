"""Fictional preview data must stay explicit, anchored, and repeatable."""
import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import report_storage
from app.db import Base
from app.models import Community, Resident, User, Vital
from app.report_answers import build_evidence
from app.report_models import MedicalReport, ReportRevision
from scripts import showcase_reports


def test_showcase_seed_preserves_native_evidence_and_is_repeatable(tmp_path, monkeypatch):
    monkeypatch.setenv("ANLIN_SHOWCASE_SEED", "1")
    monkeypatch.setattr(showcase_reports, "settings", SimpleNamespace(app_env="development"))
    monkeypatch.setattr(report_storage, "settings", SimpleNamespace(report_storage_dir=str(tmp_path / "private"), app_env="test"))
    engine = create_engine("sqlite:///" + str(tmp_path / "preview.db"))
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            community = Community(name="虚构社区")
            user = User(username="fictional", display_name="虚构展示用户", invite_code="showcase_test")
            db.add_all([community, user]); db.flush()
            resident = Resident(name="虚构王一", birth_year=1952, community_id=community.id,
                                owner_user_id=user.id, created_by_id=user.id)
            db.add(resident); db.commit()
            first = showcase_reports.seed_showcase_reports(db, resident, user); db.commit()
            second = showcase_reports.seed_showcase_reports(db, resident, user); db.commit()
            assert first == second and len(first) == 2
            reports = db.scalars(select(MedicalReport).order_by(MedicalReport.id)).all()
            assert [report.status for report in reports] == ["confirmed", "draft"]
            assert db.query(Vital).count() == 0 and db.query(ReportRevision).count() == 1
            confirmed = reports[0]
            candidate, reviewed = json.loads(confirmed.candidate_json), json.loads(confirmed.reviewed_json)
            assert "未调用 AI" in candidate["warnings"][0]
            assert "未调用模型" in db.scalar(select(ReportRevision)).reason
            evidence, _ = build_evidence(confirmed, resident, candidate, reviewed)
            assert len(reviewed["items"]) == 11
            for item in reviewed["items"]:
                assert item["id"] in evidence and evidence[item["id"]]["source_kind"] == "pdf_text"
                assert item["quote"] in candidate["pages"][item["page"] - 1]["text"]
            for report in reports:
                raw = report_storage.read_blob(report.storage_key)
                assert hashlib.sha256(raw).hexdigest() == report.sha256
    finally:
        engine.dispose()


@pytest.mark.parametrize("environment,enabled,database,name", [
    ("production", "1", "preview.db", "虚构王一"),
    ("development", "0", "preview.db", "虚构王一"),
    ("development", "1", "health.db", "虚构王一"),
    ("development", "1", "preview.db", "未标示演示"),
])
def test_showcase_seed_refuses_non_demo_target(monkeypatch, environment, enabled, database, name):
    monkeypatch.setenv("ANLIN_SHOWCASE_SEED", enabled)
    monkeypatch.setattr(showcase_reports, "settings", SimpleNamespace(app_env=environment))
    db = SimpleNamespace(get_bind=lambda: SimpleNamespace(url=SimpleNamespace(database=database)))
    with pytest.raises(RuntimeError, match="dedicated non-production"):
        showcase_reports.seed_showcase_reports(db, SimpleNamespace(name=name), None)
