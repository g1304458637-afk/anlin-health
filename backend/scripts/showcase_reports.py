"""Explicit fictional showcase reports. No OCR/model calls or real user records."""
import hashlib
import io
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from sqlalchemy import select

from app.config import settings
from app.models import Resident
from app.report_models import MedicalReport, ReportRevision
from app.report_storage import delete_blob, new_storage_key, write_blob
from app.report_types import ReportReview


SAMPLES = Path(__file__).resolve().parents[2] / "assets" / "report-samples"


def fixture_candidate(raw: bytes, expected: dict) -> dict:
    """Find exact substrings in native fixture PDF text; never fabricate a quote."""
    reader = PdfReader(io.BytesIO(raw))
    pages = [{"page": number + 1, "text": page.extract_text() or ""} for number, page in enumerate(reader.pages)]
    items, counters = [], {}
    for expected_item in expected["items"]:
        page = expected_item["page"]
        target = "".join(str(expected_item.get(key) or "") for key in ("name", "code", "value", "unit", "reference_range", "flag"))
        compact = "".join(target.split())
        pattern = re.compile(r"\s*".join(re.escape(character) for character in compact))
        match = pattern.search(pages[page - 1]["text"])
        if match is None:
            raise ValueError(f"Fictional fixture does not contain expected source row: {expected_item['code']}")
        counters[page] = counters.get(page, 0) + 1
        items.append({"id": f"p{page}-i{counters[page]}", "page": page, "quote": match.group(0),
                      **{key: expected_item[key] for key in ("name", "value", "unit", "reference_range", "flag")}})
    return {"title": "检验记录功能测试单", "patient_name": expected["patient"],
            "specimen_collected_at": expected["sample_datetime"], "reported_at": expected["report_datetime"],
            "items": items, "pages": pages,
            "warnings": ["这是虚构演示预置数据，由本地标准答案与原文匹配生成，未调用 AI，也不代表识别准确率。"],
            "model": "演示预置，未调用模型", "extraction_method": "fixture-pdf-text"}


def seed_showcase_reports(db, resident, user):
    """Called only by the explicit local showcase seeder; caller commits its DB."""
    database = str(db.get_bind().url.database or "")
    if (settings.app_env == "production" or os.getenv("ANLIN_SHOWCASE_SEED") != "1" or
            Path(database).name not in {"preview.db", "showcase.db"} or "虚构" not in resident.name):
        raise RuntimeError("Showcase reports require an explicitly enabled, dedicated non-production demo database")
    if any("虚构" not in name for name in db.scalars(select(Resident.name)).all()):
        raise RuntimeError("Showcase seeding refused: database contains residents not marked fictional")
    expected = json.loads((SAMPLES / "expected.json").read_text(encoding="utf-8"))
    if expected.get("fictional") is not True:
        raise RuntimeError("Showcase fixture must be explicitly fictional")
    original = (SAMPLES / "fictional-two-page-report.pdf").read_bytes()
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(original)))
    writer.add_metadata({"/Title": "ANLIN FICTIONAL SHOWCASE DRAFT COPY - NO MODEL USED"})
    output = io.BytesIO(); writer.write(output)
    examples = [(original, "confirmed", "虚构示例·已确认报告.pdf"),
                (output.getvalue(), "draft", "虚构示例·待核对副本.pdf")]
    ids = []
    for raw, status, filename in examples:
        sha = hashlib.sha256(raw).hexdigest()
        existing = db.scalar(select(MedicalReport).where(MedicalReport.resident_id == resident.id, MedicalReport.sha256 == sha))
        if existing:
            ids.append(existing.id)
            continue
        candidate = fixture_candidate(raw, expected)
        if resident.name != expected["patient"]:
            candidate["warnings"].append("演示原件与示例居民均为虚构，姓名不同用于展示报告归属核对，不代表真实关联。")
        now, key = datetime.now(timezone.utc), new_storage_key()
        write_blob(key, raw)
        try:
            report = MedicalReport(resident_id=resident.id, uploader_id=user.id, storage_key=key, sha256=sha,
                                   mime_type="application/pdf", filename=filename, page_count=len(candidate["pages"]),
                                   status=status, revision=1, attempts=0,
                                   candidate_json=json.dumps(candidate, ensure_ascii=False))
            db.add(report); db.flush()
            if status == "confirmed":
                review = ReportReview.model_validate({key: candidate[key] for key in (
                    "title", "patient_name", "specimen_collected_at", "reported_at", "items")} |
                    {"revision": 1, "identity_confirmed": True, "imports": []}).model_dump(mode="json")
                report.reviewed_json = json.dumps(review, ensure_ascii=False)
                report.confirmed_by_id, report.confirmed_at = user.id, now
                db.add(ReportRevision(report_id=report.id, revision=1, actor_id=user.id, actor_name=user.display_name,
                                      reason="虚构展示数据预置；未调用模型，不代表实际人工或医学核验", review_json=report.reviewed_json, created_at=now))
            ids.append(report.id)
        except BaseException:
            delete_blob(key)
            raise
    return ids
