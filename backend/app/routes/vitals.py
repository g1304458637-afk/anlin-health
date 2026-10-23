from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, update
from ..db import get_db
from ..models import User, Resident, Vital
from ..deps import auth_user, check_access, audit, is_owner
from ..schemas import VitalIn, VitalOut

router = APIRouter(prefix="/api", tags=["vitals"])


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
    now = datetime.now(timezone.utc)
    measured_at = body.measured_at or now
    if measured_at.tzinfo is None:
        raise HTTPException(422, "测量时间必须包含时区")
    if measured_at > now + timedelta(minutes=10) or measured_at < now - timedelta(days=3650):
        raise HTTPException(422, "测量时间超出允许范围")
    record = Vital(resident_id=resident_id, actor_id=user.id, **body.model_dump(exclude={"measured_at"}), measured_at=measured_at)
    db.add(record)
    db.flush()
    audit(db, user, "create_vital", "vital", record.id)
    db.commit()
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
