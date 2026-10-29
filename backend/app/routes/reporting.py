"""Reports count all accessible records; table pagination never changes totals."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_roles
from ..models import Resident, User, Vital
from ..reporting import visible_resident_ids
from ..schemas import VitalOut

router = APIRouter(prefix="/api/community", tags=["reporting"])


@router.get("/health-stats")
def health_stats(limit: int = Query(default=25, ge=1, le=100),
                 offset: int = Query(default=0, ge=0),
                 q: str = Query(default="", max_length=80),
                 user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    visible = visible_resident_ids(user.community_id)
    total = db.scalar(select(func.count()).select_from(Resident).where(Resident.id.in_(visible))) or 0
    totals = {"blood_pressure": 0, "blood_glucose": 0, "weight": 0}
    for metric, count in db.execute(select(Vital.type, func.count()).where(
            Vital.resident_id.in_(visible), Vital.deleted_at.is_(None)).group_by(Vital.type)):
        if metric in totals:
            totals[metric] = count

    today = datetime.now(timezone.utc).date()
    start = datetime.combine(today - timedelta(days=13), datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    counts = {str(day): count for day, count in db.execute(
        select(func.date(Vital.measured_at), func.count()).where(
            Vital.resident_id.in_(visible), Vital.deleted_at.is_(None),
            Vital.measured_at >= start, Vital.measured_at < end)
        .group_by(func.date(Vital.measured_at)))}
    daily = [{"date": (today - timedelta(days=i)).isoformat(),
              "count": counts.get((today - timedelta(days=i)).isoformat(), 0)} for i in range(13, -1, -1)]

    query = select(Resident).where(Resident.id.in_(visible))
    if q.strip():
        safe = q.strip().replace("%", r"\%").replace("_", r"\_")
        query = query.where(Resident.name.ilike(f"%{safe}%", escape="\\"))
    filtered_count = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    residents = db.scalars(query.order_by(Resident.id.desc()).offset(offset).limit(limit)).all()
    ids = [r.id for r in residents]
    record_counts, latest = {}, {}
    if ids:
        record_counts = dict(db.execute(select(Vital.resident_id, func.count()).where(
            Vital.resident_id.in_(ids), Vital.deleted_at.is_(None)).group_by(Vital.resident_id)).all())
        ranked = select(Vital.id, func.row_number().over(
            partition_by=(Vital.resident_id, Vital.type),
            order_by=(Vital.measured_at.desc(), Vital.id.desc())).label("rank")).where(
                Vital.resident_id.in_(ids), Vital.deleted_at.is_(None)).subquery()
        for v in db.scalars(select(Vital).join(ranked, ranked.c.id == Vital.id).where(ranked.c.rank == 1)):
            latest[(v.resident_id, v.type)] = VitalOut.model_validate(v).model_dump(mode="json")
    rows = [{"id": r.id, "name": r.name, "record_count": record_counts.get(r.id, 0),
             **{metric: latest.get((r.id, metric)) for metric in totals}} for r in residents]
    return {"resident_count": total, "totals": totals, "daily_counts": daily, "residents": rows,
            "total_residents": filtered_count, "limit": limit, "offset": offset}
