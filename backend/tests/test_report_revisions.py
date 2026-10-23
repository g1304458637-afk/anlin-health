"""Real API/DB review changes; all health data and provider outputs are synthetic."""
import copy
import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import report_storage
from app.models import ConsentEvent, Resident, User, Vital
from app.report_answers import build_evidence
from app.report_models import MedicalReport, ReportRevision, ReportVitalLink


def draft(db, monkeypatch, tmp_path, suffix="one"):
    monkeypatch.setattr(report_storage, "settings", SimpleNamespace(report_storage_dir=str(tmp_path), app_env="test"))
    user = db.scalar(select(User).where(User.username == "resident"))
    raw = ("synthetic original " + suffix).encode()
    key = report_storage.new_storage_key()
    report_storage.write_blob(key, raw)
    items = [
        {"id": "p1-i1", "name": "葡萄糖", "value": "5.6", "unit": "mmol/L", "reference_range": "3.9-6.1", "flag": "", "page": 1, "quote": "葡萄糖 5.6 mmol/L 3.9-6.1"},
        {"id": "p1-i2", "name": "误识别示例", "value": "12", "unit": "", "reference_range": "", "flag": "", "page": 1, "quote": "误识别示例 12"},
    ]
    candidate = {"title": "测试检验单", "patient_name": "虚构王一", "specimen_collected_at": "2026-10-20", "reported_at": "2026-10-21", "items": items,
                 "pages": [{"page": 1, "text": "测试检验单\n姓名 虚构王一\n采样时间 2026-10-20\n报告时间 2026-10-21\n" + "\n".join(i["quote"] for i in items)}], "extraction_method": "deepseek-text"}
    row = MedicalReport(resident_id=1, uploader_id=user.id, storage_key=key, sha256=hashlib.sha256(raw).hexdigest(),
                        mime_type="application/pdf", filename=f"sample-{suffix}.pdf", page_count=1, status="draft", revision=1,
                        candidate_json=json.dumps(candidate, ensure_ascii=False))
    db.add(row); db.commit()
    review = {key: copy.deepcopy(candidate[key]) for key in ("title", "patient_name", "specimen_collected_at", "reported_at", "items")}
    review.update(revision=1, identity_confirmed=True, imports=[])
    return row, review


def confirm(client, token, row, review):
    response = client.post(f"/api/reports/{row.id}/confirm", headers=token("resident"), json=review)
    assert response.status_code == 200, response.text
    return response.json()


def revise(client, token, report_id, reviewed, *, user="resident", reason="对照原件修正结果", **overrides):
    payload = {"expected_revision": reviewed["revision"], "reason": reason,
               "review": {**copy.deepcopy(reviewed), "imports": []}, **overrides}
    return client.post(f"/api/reports/{report_id}/revisions", headers=token(user), json=payload)


def test_manual_addition_and_exclusion_keep_real_sources_and_replay(client, token, db, monkeypatch, tmp_path):
    row, review = draft(db, monkeypatch, tmp_path)
    review["excluded_item_ids"] = ["p1-i2"]
    review["items"].append({"id": "new:manual-weight", "name": "体重", "value": "62", "unit": "kg", "source_kind": "manual", "page": 1, "quote": ""})
    result = confirm(client, token, row, review)
    manual = result["reviewed"]["items"][-1]
    assert manual["id"].startswith("m-") and manual["source_kind"] == "manual" and manual["quote"] == ""
    assert result["candidate"]["items"] == review["items"][:2]
    assert result["item_count"] == 2
    replay = confirm(client, token, row, review)
    assert replay["reviewed"] == result["reviewed"] and db.query(ReportRevision).count() == 1
    db.expire_all(); report = db.get(MedicalReport, row.id)
    evidence, context = build_evidence(report, db.get(Resident, 1), json.loads(report.candidate_json), json.loads(report.reviewed_json))
    assert "p1-i1" in evidence and "p1-i2" not in evidence and manual["id"] not in evidence
    assert all(entry["id"] != manual["id"] for entry in context)
    history = client.get(f"/api/reports/{row.id}/history", headers=token("resident"))
    assert history.headers["cache-control"] == "no-store"
    assert history.json()["items"][0]["review"]["excluded_item_ids"] == ["p1-i2"]


@pytest.mark.parametrize("variant", ["forged_quote", "downgrade_source", "missing_original", "unknown_manual", "bad_page", "duplicate_exclusion"])
def test_review_cannot_forge_or_silently_discard_sources(client, token, db, monkeypatch, tmp_path, variant):
    row, review = draft(db, monkeypatch, tmp_path)
    if variant == "forged_quote":
        review["items"].append({"id": "new:manual", "name": "体重", "value": "62", "source_kind": "manual", "quote": "伪造原文", "page": 1})
    elif variant == "downgrade_source":
        review["items"][0].update(source_kind="manual", quote="")
    elif variant == "missing_original":
        review["items"].pop()
    elif variant == "unknown_manual":
        review["items"].append({"id": "m-" + "a" * 32, "name": "体重", "value": "62", "source_kind": "manual", "quote": ""})
    elif variant == "bad_page":
        review["items"].append({"id": "new:manual", "name": "体重", "value": "62", "source_kind": "manual", "quote": "", "page": 2})
    else:
        review["excluded_item_ids"] = ["p1-i2", "p1-i2"]
    response = client.post(f"/api/reports/{row.id}/confirm", headers=token("resident"), json=review)
    assert response.status_code == 422
    assert db.query(ReportRevision).count() == 0 and db.query(Vital).count() == 0


def test_excluded_item_cannot_import(client, token, db, monkeypatch, tmp_path):
    row, review = draft(db, monkeypatch, tmp_path)
    review["excluded_item_ids"] = ["p1-i1"]
    review["imports"] = [{"item_id": "p1-i1", "type": "blood_glucose", "glucose_mmol_l": 5.6, "measured_at": datetime.now(timezone.utc).isoformat()}]
    assert client.post(f"/api/reports/{row.id}/confirm", headers=token("resident"), json=review).status_code == 422
    assert db.query(Vital).count() == 0


def test_revision_preserves_snapshots_and_flags_import_without_changing_vital(client, token, db, monkeypatch, tmp_path):
    row, review = draft(db, monkeypatch, tmp_path)
    review["imports"] = [{"item_id": "p1-i1", "type": "blood_glucose", "glucose_mmol_l": 5.6, "measured_at": datetime.now(timezone.utc).isoformat()}]
    original = confirm(client, token, row, review)
    edited = copy.deepcopy(original["reviewed"]); edited["items"][0]["value"] = "5.8"
    response = revise(client, token, row.id, edited)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["revision"] == 2 and result["affected_import_count"] == 1
    assert result["imported_vitals"][0]["needs_review"] is True
    assert result["imported_vitals"][0]["imported_revision"] == 1
    db.expire_all()
    vital = db.get(Vital, original["imported_vital_ids"][0])
    assert vital.glucose_mmol_l == 5.6 and db.query(Vital).count() == 1
    assert json.loads(db.get(MedicalReport, row.id).candidate_json) == original["candidate"]
    history = client.get(f"/api/reports/{row.id}/history", headers=token("resident")).json()["items"]
    assert [entry["revision"] for entry in history] == [2, 1]
    assert history[1]["review"] == original["reviewed"]
    assert history[0]["review"]["items"][0]["value"] == "5.8"
    assert history[0]["reason"] == "对照原件修正结果" and history[0]["actor_name"] == "resident"
    assert any(change["before"] == "5.6" and change["after"] == "5.8" for change in history[0]["changes"])
    assert revise(client, token, row.id, edited).status_code == 409
    assert client.post(f"/api/reports/{row.id}/confirm", headers=token("resident"), json=review).status_code == 409
    # Reverting a report does not silently clear an unresolved timeline review.
    restored = copy.deepcopy(result["reviewed"]); restored["items"][0]["value"] = "5.6"
    assert revise(client, token, row.id, restored, reason="再次核对原件").json()["affected_import_count"] == 1
    assert db.query(Vital).count() == 1 and db.query(ReportVitalLink).count() == 1


def test_title_only_revision_is_safe_and_revision_cannot_import(client, token, db, monkeypatch, tmp_path):
    row, review = draft(db, monkeypatch, tmp_path)
    review["imports"] = [{"item_id": "p1-i1", "type": "blood_glucose", "glucose_mmol_l": 5.6, "measured_at": datetime.now(timezone.utc).isoformat()}]
    initial = confirm(client, token, row, review)
    edited = copy.deepcopy(initial["reviewed"]); edited["title"] = "年度体检记录"
    assert revise(client, token, row.id, edited, reason=" ").status_code == 422
    assert revise(client, token, row.id, edited, review=edited).status_code == 422
    response = revise(client, token, row.id, edited)
    assert response.status_code == 200 and response.json()["affected_import_count"] == 0
    assert db.query(Vital).count() == 1
    assert revise(client, token, row.id, response.json()["reviewed"]).status_code == 422


def test_history_and_revision_follow_current_permissions(client, token, db, monkeypatch, tmp_path):
    row, review = draft(db, monkeypatch, tmp_path)
    initial = confirm(client, token, row, review)
    edited = copy.deepcopy(initial["reviewed"]); edited["title"] = "更正名称"
    for username in ("family", "admin", "staff2", "unlinked"):
        assert client.get(f"/api/reports/{row.id}/history", headers=token(username)).status_code == 404
        assert revise(client, token, row.id, edited, user=username).status_code == 404
    assert client.get(f"/api/reports/{row.id}/history", headers=token("staff")).status_code == 200
    owner = db.scalar(select(User).where(User.username == "resident"))
    db.add(ConsentEvent(resident_id=1, actor_id=owner.id, purpose="community_care", granted=False, method="resident_self", review_status="approved")); db.commit()
    assert client.get(f"/api/reports/{row.id}/history", headers=token("staff")).status_code == 404
    assert revise(client, token, row.id, edited, user="staff").status_code == 404
    assert revise(client, token, row.id, edited).status_code == 200


def test_legacy_confirmation_is_preserved_on_first_revision(client, token, db, monkeypatch, tmp_path):
    row, review = draft(db, monkeypatch, tmp_path)
    row.status = "confirmed"; row.reviewed_json = json.dumps(review, ensure_ascii=False)
    row.confirmed_by_id = db.scalar(select(User.id).where(User.username == "resident"))
    row.confirmed_at = datetime.now(timezone.utc); db.commit()
    assert db.query(ReportRevision).count() == 0
    legacy = client.get(f"/api/reports/{row.id}/history", headers=token("resident")).json()["items"][0]
    assert legacy["review"] == review and legacy["revision"] == 1
    changed = copy.deepcopy(review); changed["title"] = "已更正历史名称"
    response = revise(client, token, row.id, changed)
    assert response.status_code == 200, response.text
    assert db.query(ReportRevision).count() == 2
    snapshot = db.scalar(select(ReportRevision).where(ReportRevision.revision == 1))
    assert json.loads(snapshot.review_json) == review


def test_search_uses_current_review_excludes_omitted_items_and_filters_before_paging(client, token, db, monkeypatch, tmp_path):
    first, review = draft(db, monkeypatch, tmp_path)
    review["title"] = "年度复查甲"; review["excluded_item_ids"] = ["p1-i2"]
    confirm(client, token, first, review)
    second, _ = draft(db, monkeypatch, tmp_path, "two")
    first.created_at = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    second.created_at = datetime(2026, 10, 21, 12, tzinfo=timezone.utc); db.commit()
    headers = token("resident")
    url = "/api/residents/1/reports"
    result = client.get(url, headers=headers, params={"q": "年度 复查", "status": "confirmed", "limit": 1}).json()
    assert result["total"] == 1 and result["items"][0]["id"] == first.id
    assert client.get(url, headers=headers, params={"q": "葡萄糖"}).json()["total"] == 2
    assert client.get(url, headers=headers, params={"q": "误识别", "status": "confirmed"}).json()["total"] == 0
    assert client.get(url, headers=headers, params={"date_from": "2026-10-21", "date_to": "2026-10-21"}).json()["items"][0]["id"] == second.id
    assert client.get(url, headers=headers, params={"date_from": "2026-10-22", "date_to": "2026-10-20"}).status_code == 422
    assert client.get(url, headers=headers, params={"status": "invalid"}).status_code == 422
    assert client.get(url, headers=token("family"), params={"q": "年度"}).status_code == 404
