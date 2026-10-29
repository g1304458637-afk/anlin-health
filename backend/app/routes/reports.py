"""Private medical reports: upload, view, extract on request, and human review."""
import hashlib
import asyncio
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import Response as BytesResponse
from sqlalchemy import delete as sa_delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from cryptography.exceptions import InvalidTag

from ..db import get_db
from ..config import env_int
from ..deps import audit, auth_user, check_access, is_owner
from ..models import Resident, User, Vital
from ..ratelimit import SlidingWindowLimiter
from ..report_models import MedicalReport, ReportRevision, ReportVitalLink
from ..report_storage import delete_blob, new_storage_key, read_blob, write_blob
from ..report_types import ReportReview, ReportRevisionIn
from ..schemas import VitalIn
from .. import report_processing

router = APIRouter(prefix="/api", tags=["private reports"])
_MAX_BYTES = 10 * 1024 * 1024
_MAX_REPORTS_PER_RESIDENT = 200
_MIMES = {"application/pdf", "image/png", "image/jpeg", "image/webp"}
_NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_extract_limiter = SlidingWindowLimiter("report-extract")
_upload_limiter = SlidingWindowLimiter("report-upload")


def _accessible(db: Session, user: User, report_id: int, *, write: bool = False) -> tuple[MedicalReport, Resident]:
    report = db.get(MedicalReport, report_id)
    if report is None:
        raise HTTPException(404, "报告不存在")
    resident = check_access(db, user, db.get(Resident, report.resident_id), scope="reports", write=write)
    return report, resident


def _summary(report: MedicalReport) -> dict:
    document = json.loads(report.reviewed_json or report.candidate_json or "{}")
    return {
        "id": report.id, "resident_id": report.resident_id, "filename": report.filename,
        "mime_type": report.mime_type, "page_count": report.page_count,
        "status": report.status, "revision": report.revision, "attempts": report.attempts,
        "error": report.error, "created_at": report.created_at, "updated_at": report.updated_at,
        "confirmed_at": report.confirmed_at,
        "title": document.get("title") or report.filename,
        "item_count": len(document.get("items", [])) - len(document.get("excluded_item_ids", [])),
    }


def _detail(db: Session, report: MedicalReport, resident: Resident) -> dict:
    candidate = json.loads(report.candidate_json) if report.candidate_json else None
    reviewed = json.loads(report.reviewed_json) if report.reviewed_json else None
    links = db.scalars(select(ReportVitalLink).where(ReportVitalLink.report_id == report.id)).all()
    patient_names = [record.get("patient_name") for record in (candidate, reviewed) if isinstance(record, dict)]
    return {
        **_summary(report), "candidate": candidate, "reviewed": reviewed,
        "imported_vital_ids": [link.vital_id for link in links if link.vital_id is not None],
        "imported_vitals": [{"item_id": link.item_id, "vital_id": link.vital_id,
                             "imported_revision": link.imported_revision,
                             "needs_review": link.needs_review, "flagged_at": link.flagged_at}
                            for link in links if link.vital_id is not None],
        "deleted_import_count": sum(link.vital_id is None for link in links),
        "affected_import_count": sum(link.needs_review and link.vital_id is not None for link in links),
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
    # Uploads persist up to 10 MiB each and trigger subprocess parsing; without a
    # per-user throttle a signed-in account could fill the disk at will.
    if not _upload_limiter.hit(f"report-upload:{user.id}", env_int("REPORT_UPLOAD_RATE_PER_HOUR", 30), 3600):
        raise HTTPException(429, "上传过于频繁，请稍后再试")
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
    except report_processing.ParserEnvironmentError as exc:
        raise HTTPException(503, str(exc)) from exc
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
                 offset: int = Query(0, ge=0), q: str = Query("", max_length=100),
                 status: str | None = Query(None, pattern="^(uploaded|queued|processing|draft|failed|confirmed)$"),
                 date_from: date | None = None, date_to: date | None = None,
                 user: User = Depends(auth_user), db: Session = Depends(get_db)):
    response.headers.update(_NO_STORE)
    check_access(db, user, db.get(Resident, resident_id), scope="reports")
    query = select(MedicalReport).where(MedicalReport.resident_id == resident_id)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    if status:
        query = query.where(MedicalReport.status == status)
    # Dates refer to upload time, using the same UTC day bounds in API and UI.
    if date_from:
        query = query.where(MedicalReport.created_at >= datetime.combine(date_from, time.min, timezone.utc))
    if date_to:
        query = query.where(MedicalReport.created_at <= datetime.combine(date_to, time.max, timezone.utc))
    rows = db.scalars(query.order_by(MedicalReport.created_at.desc(), MedicalReport.id.desc())).all()
    needle = "".join(q.casefold().split())
    if needle:
        # At most 200 reports per resident; do not create a plaintext health index.
        def matches(row):
            document = json.loads(row.reviewed_json or row.candidate_json or "{}")
            excluded = set(document.get("excluded_item_ids", []))
            text = " ".join([row.filename, document.get("title") or "", *[
                " ".join(str(item.get(field) or "") for field in ("name", "value", "unit", "reference_range", "flag"))
                for item in document.get("items", []) if item.get("id") not in excluded]])
            return needle in "".join(text.casefold().split())
        rows = [row for row in rows if matches(row)]
    total = len(rows)
    rows = rows[offset:offset + limit]
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


@router.delete("/reports/{report_id}", status_code=204)
def delete_report(report_id: int, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    """Privacy deletion: removes the original file, extraction drafts, review history
    and every link. Imported vital rows are independent health entries and stay —
    they can be deleted from the vitals timeline instead. Deletion is limited to
    the resident themself (staff/admin never destroy resident documents); the audit
    log keeps only non-content metadata (filename, hash, status, page count)."""
    report = db.get(MedicalReport, report_id)
    if report is None:
        raise HTTPException(404, "报告不存在")
    resident = db.get(Resident, report.resident_id)
    check_access(db, user, resident, scope="reports")
    if not is_owner(user, resident):
        raise HTTPException(403, "仅档案本人可删除报告")
    if report.status in {"queued", "processing"}:
        raise HTTPException(409, "报告正在处理中，请稍后再删除")
    try:
        delete_blob(report.storage_key)
    except FileNotFoundError:
        pass  # already gone; finish removing the record
    except (OSError, RuntimeError, ValueError, InvalidTag):
        raise HTTPException(503, "暂时无法删除报告原件，请稍后再试") from None
    detail = {"filename": report.filename, "sha256": report.sha256, "status": report.status,
              "page_count": report.page_count, "revision": report.revision,
              "confirmed": report.status == "confirmed"}
    db.execute(sa_delete(ReportVitalLink).where(ReportVitalLink.report_id == report.id))
    db.execute(sa_delete(ReportRevision).where(ReportRevision.report_id == report.id))
    db.delete(report)
    audit(db, user, "delete_report", "medical_report", report.id, detail)
    db.commit()


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
    except report_processing.ParserEnvironmentError as exc:
        raise HTTPException(503, str(exc)) from exc
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


def _review_payload(report: MedicalReport, body: ReportReview, previous: dict | None = None) -> dict:
    """Keep every extraction anchor; exclusions are explicit and additions are manual."""
    if not body.identity_confirmed:
        raise HTTPException(422, "请先确认报告身份")
    review = body.model_dump(mode="json")
    candidate = json.loads(report.candidate_json or "{}")
    original_items = {item["id"]: item for item in candidate.get("items", [])}
    if len(original_items) != len(candidate.get("items", [])):
        raise HTTPException(422, "原始项目来源重复，请重新核对")
    known_manual = {item["id"] for item in (previous or {}).get("items", [])
                    if item.get("source_kind") == "manual"}
    supplied_ids = [item["id"] for item in review["items"]]
    if len(supplied_ids) != len(set(supplied_ids)):
        raise HTTPException(422, "报告项目不能重复")
    id_map = {}
    for item in review["items"]:
        item_id = item["id"]
        if item_id in original_items:
            original = original_items[item_id]
            if item["source_kind"] != "extracted" or any(item[field] != original[field] for field in ("page", "quote")):
                raise HTTPException(422, "报告项目的来源页和原文不可修改")
        else:
            if item["source_kind"] != "manual" or item["quote"]:
                raise HTTPException(422, "新增项目必须明确标记为人工补录，不能伪造原文")
            if re.fullmatch(r"new:[A-Za-z0-9-]{1,36}", item_id):
                # Stable, server-derived IDs also make initial-confirmation retries idempotent.
                item["id"] = "m-" + hashlib.sha256(f"{report.id}:{item_id}".encode()).hexdigest()[:32]
                id_map[item_id] = item["id"]
            elif item_id not in known_manual:
                raise HTTPException(422, "人工项目编号无效，请重新打开报告")
        if item["page"] is not None and item["page"] > report.page_count:
            raise HTTPException(422, "项目定位页码超出报告范围")
    ids = {item["id"] for item in review["items"]}
    if len(ids) != len(review["items"]) or not set(original_items).issubset(ids) or not known_manual.issubset(ids):
        raise HTTPException(422, "请用排除标记移除误识别项目，保留来源历史")
    excluded = [id_map.get(item_id, item_id) for item_id in review["excluded_item_ids"]]
    if len(excluded) != len(set(excluded)) or not set(excluded).issubset(ids):
        raise HTTPException(422, "排除的项目无效或重复")
    review["excluded_item_ids"] = excluded
    for imp in review["imports"]:
        imp["item_id"] = id_map.get(imp["item_id"], imp["item_id"])
    return review


def _import_snapshot(review: dict, item_id: str) -> dict:
    item = next((item for item in review["items"] if item["id"] == item_id), None)
    if item_id in review.get("excluded_item_ids", []):
        item = None
    return {"item": ({field: item.get(field) for field in ("name", "value", "unit", "reference_range", "flag")}
                     if item else None),
            **{field: review.get(field) for field in ("patient_name", "specimen_collected_at", "reported_at")}}


def _snapshot(report_id, revision, actor, reason, review, when):
    return ReportRevision(report_id=report_id, revision=revision,
                          actor_id=actor.id if actor else None,
                          actor_name=actor.display_name if actor else "历史确认人",
                          reason=reason, review_json=json.dumps(review, ensure_ascii=False), created_at=when)


def _diff_reviews(before: dict | None, after: dict) -> list[dict]:
    changes = []
    if before is None:
        return [{"kind": "initial", "label": "首次确认", "before": None, "after": after["title"]}]
    labels = {"title": "报告名称", "patient_name": "报告患者姓名", "specimen_collected_at": "采样时间", "reported_at": "报告时间"}
    for key, label in labels.items():
        if before.get(key) != after.get(key):
            changes.append({"kind": "header", "label": label, "before": before.get(key), "after": after.get(key)})
    previous = {item["id"]: item for item in before["items"]}
    old_excluded, new_excluded = set(before.get("excluded_item_ids", [])), set(after.get("excluded_item_ids", []))
    field_labels = {"name": "名称", "value": "结果", "unit": "单位", "reference_range": "参考范围", "flag": "标记", "page": "人工定位页"}
    for item in after["items"]:
        item_id, old = item["id"], previous.get(item["id"])
        if old is None:
            changes.append({"kind": "manual_add", "item_id": item_id, "label": item["name"], "before": None, "after": item["value"] + " " + item["unit"]})
        else:
            for field, label in field_labels.items():
                if old.get(field) != item.get(field):
                    changes.append({"kind": "field", "item_id": item_id, "label": item["name"] + " · " + label,
                                    "before": old.get(field), "after": item.get(field)})
        if (item_id in old_excluded) != (item_id in new_excluded):
            changes.append({"kind": "excluded", "item_id": item_id, "label": item["name"],
                            "before": "已排除" if item_id in old_excluded else "保留",
                            "after": "已排除" if item_id in new_excluded else "保留"})
    return changes


@router.get("/reports/{report_id}/history")
def report_history(report_id: int, response: Response, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    response.headers.update(_NO_STORE)
    report, _ = _accessible(db, user, report_id)
    rows = db.scalars(select(ReportRevision).where(ReportRevision.report_id == report_id)
                      .order_by(ReportRevision.revision)).all()
    if not rows and report.reviewed_json:
        rows = [_snapshot(report.id, report.revision, db.get(User, report.confirmed_by_id) if report.confirmed_by_id else None,
                          "首次人工确认（历史记录）", json.loads(report.reviewed_json), report.confirmed_at or report.updated_at)]
    result, previous = [], None
    for row in rows:
        review = json.loads(row.review_json)
        result.append({"revision": row.revision, "actor_id": row.actor_id, "actor_name": row.actor_name,
                       "reason": row.reason, "created_at": row.created_at, "review": review,
                       "changes": _diff_reviews(previous, review)})
        previous = review
    audit(db, user, "read_report_history", "medical_report", report.id)
    db.commit()
    return {"report_id": report.id, "current_revision": report.revision, "items": list(reversed(result))}


@router.post("/reports/{report_id}/confirm")
def confirm_report(report_id: int, body: ReportReview, response: Response,
                   user: User = Depends(auth_user), db: Session = Depends(get_db)):
    response.headers.update(_NO_STORE)
    report, resident = _accessible(db, user, report_id, write=True)
    previous = json.loads(report.reviewed_json) if report.reviewed_json else None
    review = _review_payload(report, body, previous)
    if report.status == "confirmed" and report.revision == body.revision:
        if previous and ReportReview.model_validate(previous).model_dump(mode="json") == review:
            return _detail(db, report, resident)
        raise HTTPException(409, "报告已确认，不能再修改")
    if report.status != "draft" or report.revision != body.revision or not report.candidate_json:
        raise HTTPException(409, "报告版本已变化，请重新载入")
    reviewed_items = {item["id"]: item for item in review["items"]}
    imports = review.get("imports", [])
    if len({imp["item_id"] for imp in imports}) != len(imports):
        raise HTTPException(422, "同一项目只能导入一次")
    validated = []
    for imp in imports:
        item = reviewed_items.get(imp["item_id"])
        if item is None or imp["item_id"] in review["excluded_item_ids"]:
            raise HTTPException(422, "导入项目不在报告中或已被排除")
        validated.append((imp, _validate_import(imp, item)))
    _verified_data(report)
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
        db.add(ReportVitalLink(report_id=report.id, item_id=imp["item_id"], vital_id=vital.id,
                              imported_revision=report.revision,
                              imported_snapshot_json=json.dumps(_import_snapshot(review, imp["item_id"]), ensure_ascii=False)))
    db.add(_snapshot(report.id, report.revision, user, "首次人工确认", review, now))
    audit(db, user, "confirm_report", "medical_report", report.id)
    db.commit()
    db.refresh(report)
    return _detail(db, report, resident)


@router.post("/reports/{report_id}/revisions")
def revise_report(report_id: int, body: ReportRevisionIn, response: Response,
                  user: User = Depends(auth_user), db: Session = Depends(get_db)):
    response.headers.update(_NO_STORE)
    report, resident = _accessible(db, user, report_id, write=True)
    if report.status != "confirmed" or report.revision != body.expected_revision or not report.reviewed_json:
        raise HTTPException(409, "报告版本已变化，请重新打开后修订")
    if body.review.revision != body.expected_revision:
        raise HTTPException(409, "核对版本与当前报告不一致")
    if body.review.imports:
        raise HTTPException(422, "修订不会新增或覆盖健康记录，请在健康记录中单独核对")
    previous = json.loads(report.reviewed_json)
    review = _review_payload(report, body.review, previous)
    if not _diff_reviews(previous, review):
        raise HTTPException(422, "尚未修改报告内容")
    _verified_data(report)
    old_revision, old_actor, old_time = report.revision, report.confirmed_by_id, report.confirmed_at or report.updated_at
    now = datetime.now(timezone.utc)
    review["revision"] = old_revision + 1
    changed = db.execute(update(MedicalReport).where(
        MedicalReport.id == report.id, MedicalReport.status == "confirmed", MedicalReport.revision == old_revision
    ).values(revision=old_revision + 1, reviewed_json=json.dumps(review, ensure_ascii=False),
             confirmed_by_id=user.id, confirmed_at=now, updated_at=now).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "报告已被其他人修订，请重新打开")
    if not db.scalar(select(ReportRevision.id).where(ReportRevision.report_id == report.id,
                                                   ReportRevision.revision == old_revision)):
        db.add(_snapshot(report.id, old_revision, db.get(User, old_actor) if old_actor else None,
                         "首次人工确认（历史记录）", previous, old_time))
    for link in db.scalars(select(ReportVitalLink).where(ReportVitalLink.report_id == report.id)).all():
        baseline = json.loads(link.imported_snapshot_json) if link.imported_snapshot_json else _import_snapshot(previous, link.item_id)
        if link.imported_snapshot_json is None:
            link.imported_snapshot_json = json.dumps(baseline, ensure_ascii=False)
        if baseline != _import_snapshot(review, link.item_id) and not link.needs_review:
            link.needs_review, link.flagged_at = True, now
    db.add(_snapshot(report.id, old_revision + 1, user, body.reason, review, now))
    audit(db, user, "revise_report", "medical_report", report.id)
    db.commit()
    db.refresh(report)
    return _detail(db, report, resident)
