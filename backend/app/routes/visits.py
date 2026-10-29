from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from ..db import get_db
from ..models import User, Resident, Visit
from ..deps import require_roles, auth_user, check_access, is_owner, audit
from ..schemas import VisitIn, VisitOut, VisitFinish
from ..reporting import visible_resident_ids

router = APIRouter(prefix="/api", tags=["visits"])


@router.get("/community/visits", response_model=list[VisitOut])
def staff_visits(limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0),
                 status: Literal["planned", "done"] | None = None,
                 user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    query = select(Visit).where(Visit.resident_id.in_(visible_resident_ids(user.community_id)))
    if status is not None:
        query = query.where(Visit.status == status)
    return db.scalars(query.order_by(Visit.due_date.desc(), Visit.id.desc()).offset(offset).limit(limit)).all()


@router.get("/community/visits/stats")
def visit_stats(user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    visible = visible_resident_ids(user.community_id)
    counts = dict(db.execute(select(Visit.status, func.count()).where(
        Visit.resident_id.in_(visible)).group_by(Visit.status)).all())
    overdue = db.scalar(select(func.count()).select_from(Visit).where(
        Visit.resident_id.in_(visible), Visit.status == "planned",
        Visit.due_date < datetime.now(timezone.utc).date())) or 0
    return {"total": sum(counts.values()), "planned": counts.get("planned", 0),
            "done": counts.get("done", 0), "overdue": overdue}


@router.post("/community/visits", response_model=VisitOut, status_code=201)
def create_visit(body: VisitIn, user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    check_access(db, user, db.get(Resident, body.resident_id), scope="visits", write=True)
    visit = Visit(resident_id=body.resident_id, staff_id=user.id, due_date=body.due_date, note=body.note, status="planned")
    db.add(visit)
    db.flush()
    audit(db, user, "create_visit", "visit", visit.id)
    db.commit()
    return visit


@router.patch("/community/visits/{visit_id}/finish", response_model=VisitOut)
def finish_visit(visit_id: int, body: VisitFinish, user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    v = db.get(Visit, visit_id)
    if not v:
        raise HTTPException(404, "任务不存在")
    check_access(db, user, db.get(Resident, v.resident_id), scope="visits", write=True)
    if v.staff_id != user.id:
        raise HTTPException(403, "只能完成自己负责的走访")
    if v.status != "done":
        v.status = "done"
        v.note = body.note
        v.finished_at = datetime.now(timezone.utc)
        audit(db, user, "complete_visit", "visit", v.id)
        db.commit()
    return v


@router.get("/residents/{resident_id}/visits")
def self_visits(resident_id: int, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    resident = db.get(Resident, resident_id)
    if not resident or not is_owner(user, resident):
        raise HTTPException(404, "无权查看")
    visits = db.scalars(select(Visit).where(Visit.resident_id == resident_id).order_by(Visit.due_date.desc()).limit(50)).all()
    # Summary may contain sensitive staff notes, not exposed in resident channel by default.
    return [{"id": v.id, "due_date": v.due_date, "status": v.status} for v in visits]


@router.get("/admin/summary")
def admin_summary(user: User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    from ..models import Community, ConsentEvent
    communities = db.scalars(select(Community).order_by(Community.id)).all()
    # Two flat scans instead of one consent query per resident (the previous shape
    # degraded linearly with community size). Later events win; an unreviewed paper
    # event never counts as active.
    latest: dict[int, bool] = {}
    for resident_id, granted, review_status in db.execute(
            select(ConsentEvent.resident_id, ConsentEvent.granted, ConsentEvent.review_status)
            .where(ConsentEvent.purpose == "community_care").order_by(ConsentEvent.id)):
        latest[resident_id] = bool(granted) and review_status == "approved"
    active_counts: dict[int, int] = {}
    for resident_id, community_id in db.execute(select(Resident.id, Resident.community_id)):
        if latest.get(resident_id):
            active_counts[community_id] = active_counts.get(community_id, 0) + 1
    return {"community_count": len(communities), "communities": [
        {"id": c.id, "name": c.name, "active_resident_count": active_counts.get(c.id, 0)}
        for c in communities]}
