from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from ..db import get_db
from ..models import User, Resident, Vital
from ..deps import auth_user, check_access, audit, is_owner
from ..schemas import VitalIn, VitalOut

router = APIRouter(prefix="/api", tags=["vitals"])


def _as_utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _replay(db, user, resident_id, body):
    if not body.client_request_id:
        return None
    existing = db.scalar(select(Vital).where(Vital.resident_id == resident_id, Vital.actor_id == user.id,
                                            Vital.client_request_id == body.client_request_id))
    if existing:
        fields = ("type", "systolic", "diastolic", "glucose_mmol_l", "weight_kg", "measurement_context")
        if any(getattr(existing, key) != getattr(body, key) for key in fields) or (
                body.measured_at is not None and _as_utc(existing.measured_at) != _as_utc(body.measured_at)):
            raise HTTPException(409, "这次提交已保存为其他内容，请刷新记录后再修改")
    return existing


@router.get("/residents/{resident_id}/vitals/series")
def vital_series(resident_id: int, metric: str = Query(pattern="^(blood_pressure|blood_glucose|weight)$"),
                 start_date: date = Query(), end_date: date = Query(), tz: str = Query(default="Asia/Shanghai", max_length=80),
                 user: User = Depends(auth_user), db: Session = Depends(get_db)):
    check_access(db, user, db.get(Resident, resident_id), scope="vitals")
    if end_date < start_date or (end_date - start_date).days > 365:
        raise HTTPException(422, "请选择不超过366天的有效日期范围")
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "时区无效") from None
    try:
        start = datetime.combine(start_date, time.min, zone).astimezone(timezone.utc)
        end = datetime.combine(end_date + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
    except (OverflowError, ValueError):
        raise HTTPException(422, "日期范围超出支持范围") from None
    records = db.scalars(select(Vital).where(Vital.resident_id == resident_id, Vital.type == metric,
        Vital.measured_at >= start, Vital.measured_at < end).order_by(Vital.measured_at, Vital.id).limit(5001)).all()
    if len(records) > 5000:
        raise HTTPException(422, "这段时间的记录超过5000条，请缩小日期范围后查看完整趋势")
    from ..report_models import ReportVitalLink
    links = {link.vital_id: link for link in db.scalars(select(ReportVitalLink).join(Vital, Vital.id == ReportVitalLink.vital_id)
             .where(Vital.resident_id == resident_id, Vital.type == metric, Vital.measured_at >= start, Vital.measured_at < end)).all()}
    points = []
    for row in records:
        point = VitalOut.model_validate(row).model_dump(mode="json")
        point["measured_at"] = _as_utc(row.measured_at).isoformat()
        point["created_at"] = _as_utc(row.created_at).isoformat()
        link = links.get(row.id)
        point["source"] = "report" if link else "manual"
        if user.role != "family":
            point["report_id"] = link.report_id if link else None
            point["source_needs_review"] = bool(getattr(link, "needs_review", False)) if link else False
        points.append(point)
    return {"metric": metric, "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
            "timezone": tz, "window_start": start.isoformat(), "window_end": end.isoformat(),
            "count": len(points), "points": points}


@router.get("/residents/{resident_id}/vitals", response_model=list[VitalOut])
def list_vitals(resident_id: int, metric: str | None = Query(default=None, pattern="^(blood_pressure|blood_glucose|weight)$"), limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0), user: User = Depends(auth_user), db: Session = Depends(get_db)):
    check_access(db, user, db.get(Resident, resident_id), scope="vitals")
    query = select(Vital).where(Vital.resident_id == resident_id)
    if metric:
        query = query.where(Vital.type == metric)
    return db.scalars(query.order_by(Vital.measured_at.desc(), Vital.id.desc()).offset(offset).limit(limit)).all()


@router.post("/residents/{resident_id}/vitals", response_model=VitalOut, status_code=201)
def create_vital(resident_id: int, body: VitalIn, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    check_access(db, user, db.get(Resident, resident_id), scope="vitals", write=True)
    if body.measured_at is not None and body.measured_at.tzinfo is None:
        raise HTTPException(422, "测量时间必须包含时区")
    existing = _replay(db, user, resident_id, body)
    if existing:
        return existing
    now = datetime.now(timezone.utc)
    measured_at = body.measured_at or now
    if measured_at.tzinfo is None:
        raise HTTPException(422, "测量时间必须包含时区")
    if measured_at > now + timedelta(minutes=10) or measured_at < now - timedelta(days=3650):
        raise HTTPException(422, "测量时间超出允许范围")
    measured_at = measured_at.astimezone(timezone.utc)
    record = Vital(resident_id=resident_id, actor_id=user.id, **body.model_dump(exclude={"measured_at"}), measured_at=measured_at)
    try:
        db.add(record)
        db.flush()
        audit(db, user, "create_vital", "vital", record.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = _replay(db, user, resident_id, body)
        if existing:
            return existing
        raise
    return record


@router.delete("/residents/{resident_id}/vitals/{vital_id}", status_code=204)
def delete_vital(resident_id: int, vital_id: int, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    resident = check_access(db, user, db.get(Resident, resident_id), scope="vitals", write=True)
    record = db.get(Vital, vital_id)
    if not record or record.resident_id != resident_id:
        raise HTTPException(404, "记录不存在")
    # Only resident owner can delete records; staff entries are audit-tracked and reviewed separately.
    if not is_owner(user, resident):
        raise HTTPException(403, "仅档案本人可删除记录")
    audit(db, user, "delete_vital", "vital", record.id)
    from ..report_models import ReportVitalLink
    # Keep the report/item import tombstone while allowing the owner to remove
    # the timeline entry, including in SQLite where FK actions may be disabled.
    db.execute(update(ReportVitalLink).where(ReportVitalLink.vital_id == record.id).values(vital_id=None))
    db.delete(record)
    db.commit()
