"""Private medical reports: upload, view, extract on request, and human review."""
import hashlib
import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import Response as BytesResponse
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from cryptography.exceptions import InvalidTag

from ..db import get_db
from ..deps import audit, auth_user, check_access
from ..models import Resident, User, Vital
from ..ratelimit import SlidingWindowLimiter
from ..report_models import MedicalReport, ReportVitalLink
from ..report_storage import delete_blob, new_storage_key, read_blob, write_blob
from ..report_types import ReportReview
from ..schemas import VitalIn
from .. import report_processing

router = APIRouter(prefix="/api", tags=["private reports"])
_MAX_BYTES = 10 * 1024 * 1024
_MAX_REPORTS_PER_RESIDENT = 200
_MIMES = {"application/pdf", "image/png", "image/jpeg", "image/webp"}
_NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_extract_limiter = SlidingWindowLimiter()


def _accessible(db: Session, user: User, report_id: int, *, write: bool = False) -> tuple[MedicalReport, Resident]:
    report = db.get(MedicalReport, report_id)
    if report is None:
        raise HTTPException(404, "报告不存在")
    resident = check_access(db, user, db.get(Resident, report.resident_id), scope="reports", write=write)
    return report, resident


def _summary(report: MedicalReport) -> dict:
    return {
        "id": report.id, "resident_id": report.resident_id, "filename": report.filename,
        "mime_type": report.mime_type, "page_count": report.page_count,
        "status": report.status, "revision": report.revision, "attempts": report.attempts,
        "error": report.error, "created_at": report.created_at, "updated_at": report.updated_at,
        "confirmed_at": report.confirmed_at,
    }


def _detail(db: Session, report: MedicalReport, resident: Resident) -> dict:
    candidate = json.loads(report.candidate_json) if report.candidate_json else None
    reviewed = json.loads(report.reviewed_json) if report.reviewed_json else None
    links = db.execute(select(ReportVitalLink.item_id, ReportVitalLink.vital_id).where(
        ReportVitalLink.report_id == report.id)).all()
    patient_names = [record.get("patient_name") for record in (candidate, reviewed) if isinstance(record, dict)]
    return {
        **_summary(report), "candidate": candidate, "reviewed": reviewed,
        "imported_vital_ids": [vital_id for _, vital_id in links if vital_id is not None],
        "imported_vitals": [{"item_id": item_id, "vital_id": vital_id} for item_id, vital_id in links
                            if vital_id is not None],
        "deleted_import_count": sum(vital_id is None for _, vital_id in links),
        "name_match_warning": any(name and "".join(name.split()) != "".join(resident.name.split())
                                  for name in patient_names),
    }


def _safe_filename(filename: str) -> str:
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    filename = "".join(ch for ch in filename if ch.isprintable() and ch not in "\x00\r\n")
    if not filename or len(filename) > 180:
        raise HTTPException(422, "文件名无效")
    return filename


def _verified_data(report: MedicalReport) -> bytes:
    try:
        data = read_blob(report.storage_key)
    except (OSError, RuntimeError, ValueError, InvalidTag):
        raise HTTPException(503, "暂时无法读取报告原件") from None
    if hashlib.sha256(data).hexdigest() != report.sha256:
        raise HTTPException(409, "报告原件完整性校验失败，请联系管理员")
    return data


@router.post("/residents/{resident_id}/reports", status_code=201)
async def upload_report(resident_id: int, request: Request, response: Response, filename: str = Query(...),
                        user: User = Depends(auth_user), db: Session = Depends(get_db)):
    response.headers.update(_NO_STORE)
    check_access(db, user, db.get(Resident, resident_id), scope="reports", write=True)
    filename = _safe_filename(filename)
    declared_mime = request.headers.get("content-type", "").split(";", 1)[0].lower().strip()
    if declared_mime not in _MIMES:
        raise HTTPException(415, "仅支持 PDF、PNG、JPEG、WebP")
    length = request.headers.get("content-length")
    if length and length.isdecimal() and int(length) > _MAX_BYTES:
        raise HTTPException(413, "文件不能超过 10 MiB")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > _MAX_BYTES:
            raise HTTPException(413, "文件不能超过 10 MiB")
    if not data:
        raise HTTPException(422, "文件为空")
    raw = bytes(data)
    sha = hashlib.sha256(raw).hexdigest()
    existing = db.scalar(select(MedicalReport).where(MedicalReport.resident_id == resident_id,
                                                      MedicalReport.sha256 == sha))
    if existing:
        if declared_mime != existing.mime_type:
            raise HTTPException(415, "文件格式与 Content-Type 不一致")
        audit(db, user, "read_report_duplicate", "medical_report", existing.id)
        db.commit()
        return _detail(db, existing, db.get(Resident, resident_id))
    count = db.scalar(select(func.count()).select_from(MedicalReport).where(MedicalReport.resident_id == resident_id)) or 0
    if count >= _MAX_REPORTS_PER_RESIDENT:
        raise HTTPException(429, "报告数量已达上限")
    try:
        inspected = await asyncio.to_thread(report_processing.inspect_document, raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    actual_mime = inspected["mime_type"]
    if actual_mime != declared_mime or actual_mime not in _MIMES:
        raise HTTPException(415, "文件格式与 Content-Type 不一致")
    pages = int(inspected["page_count"])
    if not 1 <= pages <= 5:
        raise HTTPException(422, "报告页数超出范围")
    key = new_storage_key()
    write_blob(key, raw)
    report = MedicalReport(resident_id=resident_id, uploader_id=user.id, storage_key=key,
                           sha256=sha, mime_type=actual_mime, filename=filename,
                           page_count=pages, status="uploaded", revision=0, attempts=0)
    try:
        db.add(report)
        db.flush()
        audit(db, user, "upload_report", "medical_report", report.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        delete_blob(key)
        existing = db.scalar(select(MedicalReport).where(MedicalReport.resident_id == resident_id,
                                                          MedicalReport.sha256 == sha))
        if existing:
            return _detail(db, existing, db.get(Resident, resident_id))
        raise
    except BaseException:
        db.rollback()
        delete_blob(key)
        raise
    return _detail(db, report, db.get(Resident, resident_id))


@router.get("/residents/{resident_id}/reports")
def list_reports(resident_id: int, response: Response, limit: int = Query(20, ge=1, le=100),
                 offset: int = Query(0, ge=0), user: User = Depends(auth_user), db: Session = Depends(get_db)):
    response.headers.update(_NO_STORE)
    check_access(db, user, db.get(Resident, resident_id), scope="reports")
    query = select(MedicalReport).where(MedicalReport.resident_id == resident_id)
    total = db.scalar(select(func.count()).select_from(MedicalReport).where(MedicalReport.resident_id == resident_id)) or 0
    rows = db.scalars(query.order_by(MedicalReport.created_at.desc(), MedicalReport.id.desc()).offset(offset).limit(limit)).all()
    audit(db, user, "list_reports", "resident", resident_id)
    db.commit()
    return {"items": [_summary(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/reports/{report_id}")
def get_report(report_id: int, response: Response, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    response.headers.update(_NO_STORE)
    report, resident = _accessible(db, user, report_id)
    result = _detail(db, report, resident)
    audit(db, user, "read_report", "medical_report", report.id)
    db.commit()
    return result


@router.get("/reports/{report_id}/original")
def original_report(report_id: int, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    report, _ = _accessible(db, user, report_id)
    data = _verified_data(report)
    audit(db, user, "read_report_original", "medical_report", report.id)
    db.commit()
    return BytesResponse(data, media_type=report.mime_type, headers=_NO_STORE)


@router.get("/reports/{report_id}/pages/{page}")
def report_page(report_id: int, page: int, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    report, _ = _accessible(db, user, report_id)
    if page < 1 or page > report.page_count:
        raise HTTPException(404, "报告页不存在")
    try:
        png = report_processing.render_page(_verified_data(report), page)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, user, "read_report_page", "medical_report", report.id)
    db.commit()
    return BytesResponse(png, media_type="image/png", headers=_NO_STORE)


@router.post("/reports/{report_id}/extract", status_code=202)
def request_extraction(report_id: int, body: dict, response: Response,
                       user: User = Depends(auth_user), db: Session = Depends(get_db)):
    response.headers.update(_NO_STORE)
    report, _ = _accessible(db, user, report_id, write=True)
    if body.get("allow_cloud") is not True:
        raise HTTPException(422, "需明确同意将报告发送至 DeepSeek 进行提取")
    if report.status == "queued":
        return _summary(report)
    if report.status not in {"uploaded", "failed"}:
        raise HTTPException(409, "当前状态不能重新提取")
    if report.attempts >= 3:
        raise HTTPException(409, "提取重试次数已达上限")
    if not _extract_limiter.hit(f"report-extract:{user.id}", 3, 60):
        raise HTTPException(429, "请求过于频繁")
    changed = db.execute(update(MedicalReport).where(
        MedicalReport.id == report.id, MedicalReport.status.in_(["uploaded", "failed"]),
        MedicalReport.attempts < 3).values(status="queued", requested_by_id=user.id,
                                           requested_token_version=user.token_version,
                                           error=None, lease_token=None, lease_until=None,
                                           updated_at=datetime.now(timezone.utc)))
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "报告状态已变更")
    audit(db, user, "request_report_extraction", "medical_report", report.id)
    db.commit()
    db.refresh(report)
    return _summary(report)


def _decimal(text: str) -> Decimal:
    try:
        value = Decimal(str(text).strip().replace(",", ""))
        if not value.is_finite():
            raise InvalidOperation
        return value
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise HTTPException(422, "导入值必须与报告中的数值一致") from exc


def _validate_import(imp: dict, item: dict) -> VitalIn:
    name = re.sub(r"\s+", "", item["name"]).upper()
    unit = re.sub(r"\s+", "", item["unit"]).lower()
    raw = item["value"].strip()
    kind = imp["type"]
    if kind == "blood_glucose":
        if name not in {"葡萄糖", "血糖", "GLU"} or unit != "mmol/l":
            raise HTTPException(422, "仅可导入 mmol/L 血糖结果")
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw):
            raise HTTPException(422, "血糖报告值不是单一数值")
        if _decimal(raw) != _decimal(imp.get("glucose_mmol_l")):
            raise HTTPException(422, "血糖导入值与报告不一致")
    elif kind == "weight":
        if name not in {"体重", "WEIGHT"} or unit != "kg":
            raise HTTPException(422, "仅可导入 kg 体重结果")
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw):
            raise HTTPException(422, "体重报告值不是单一数值")
        if _decimal(raw) != _decimal(imp.get("weight_kg")):
            raise HTTPException(422, "体重导入值与报告不一致")
    elif kind == "blood_pressure":
        if name not in {"血压", "BP"} or unit != "mmhg":
            raise HTTPException(422, "仅可导入 mmHg 血压结果")
        match = re.fullmatch(r"\s*(\d+)\s*[/／]\s*(\d+)\s*", raw)
        if not match or int(match[1]) != imp.get("systolic") or int(match[2]) != imp.get("diastolic"):
            raise HTTPException(422, "血压导入值与报告不一致")
    else:
        raise HTTPException(422, "不支持导入该指标")
    measured_at = imp.get("measured_at")
    if isinstance(measured_at, str):
        try:
            measured_at = datetime.fromisoformat(measured_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(422, "测量时间无效") from exc
    now = datetime.now(timezone.utc)
    if measured_at is None or measured_at.tzinfo is None or measured_at > now + timedelta(minutes=10) or measured_at < now - timedelta(days=3650):
        raise HTTPException(422, "测量时间超出允许范围")
    try:
        return VitalIn.model_validate({key: value for key, value in imp.items() if key != "item_id"})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/reports/{report_id}/confirm")
def confirm_report(report_id: int, body: ReportReview, response: Response,
                   user: User = Depends(auth_user), db: Session = Depends(get_db)):
    response.headers.update(_NO_STORE)
    report, resident = _accessible(db, user, report_id, write=True)
    review = body.model_dump(mode="json")
    if report.status == "confirmed" and report.revision == body.revision:
        if report.reviewed_json and json.loads(report.reviewed_json) == review:
            return _detail(db, report, resident)
        raise HTTPException(409, "报告已确认，不能再修改")
    if report.status != "draft" or report.revision != body.revision or not report.candidate_json:
        raise HTTPException(409, "报告版本已变化，请重新载入")
    if not body.identity_confirmed:
        raise HTTPException(422, "请先确认报告身份")
    candidate = json.loads(report.candidate_json)
    original_items = {item["id"]: item for item in candidate.get("items", [])}
    reviewed_items = {item["id"]: item for item in review["items"]}
    if (len(original_items) != len(candidate.get("items", [])) or
            len(reviewed_items) != len(review["items"]) or original_items.keys() != reviewed_items.keys()):
        raise HTTPException(422, "报告项目不能增删或重复")
    for item_id, item in reviewed_items.items():
        original = original_items[item_id]
        if any(item[field] != original[field] for field in ("id", "page", "quote")):
            raise HTTPException(422, "报告项目的来源页和原文不可修改")
    imports = review.get("imports", [])
    if len({imp["item_id"] for imp in imports}) != len(imports):
        raise HTTPException(422, "同一项目只能导入一次")
    validated = []
    for imp in imports:
        item = reviewed_items.get(imp["item_id"])
        if item is None:
            raise HTTPException(422, "导入项目不在报告中")
        validated.append((imp, _validate_import(imp, item)))
    now = datetime.now(timezone.utc)
    changed = db.execute(update(MedicalReport).where(
        MedicalReport.id == report.id, MedicalReport.status == "draft", MedicalReport.revision == body.revision
    ).values(status="confirmed", reviewed_json=json.dumps(review, ensure_ascii=False),
             confirmed_by_id=user.id, confirmed_at=now, updated_at=now))
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "报告版本已变化，请重新载入")
    for imp, vital_in in validated:
        vital = Vital(resident_id=report.resident_id, actor_id=user.id,
                      **vital_in.model_dump(exclude={"measured_at"}), measured_at=vital_in.measured_at)
        db.add(vital)
        db.flush()
        db.add(ReportVitalLink(report_id=report.id, item_id=imp["item_id"], vital_id=vital.id))
    audit(db, user, "confirm_report", "medical_report", report.id)
    db.commit()
    db.refresh(report)
    return _detail(db, report, resident)
