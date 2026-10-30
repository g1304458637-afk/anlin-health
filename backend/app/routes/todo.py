"""Console overview aggregation: one prioritized to-do list across four boards."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_roles
from ..models import Appointment, ClaimRequest, ConsentEvent, Resident, RiskAlert, User, Visit
from ..reporting import visible_resident_ids

router = APIRouter(prefix="/api/community", tags=["today todo"])

# Lower number = more urgent; the console renders them in this order.
PRIORITY = {"critical_alert": 0, "overdue_visit": 1, "warning_alert": 2,
            "pending_appointment": 3, "pending_claim": 4, "pending_consent": 5}


@router.get("/today-todo")
def today_todo(response: Response, user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    visible = visible_resident_ids(user.community_id)
    names = {r.id: r.name for r in db.scalars(select(Resident).where(
        Resident.id.in_(visible))).all()}
    items: list[dict] = []

    open_alerts = db.scalars(select(RiskAlert).where(
        RiskAlert.resident_id.in_(visible), RiskAlert.acknowledged_at.is_(None))
        .order_by(RiskAlert.level.is_("warning"), RiskAlert.created_at.desc()).limit(20)).all()
    for a in open_alerts:
        level = "critical_alert" if a.level == "critical" else "warning_alert"
        items.append({"kind": level, "priority": PRIORITY[level], "ref_id": a.id,
                      "resident": names.get(a.resident_id, f"#{a.resident_id}"),
                      "label": f"风险预警：{a.value_text}"})

    today = datetime.now(timezone.utc).date()
    overdue = db.scalars(select(Visit).where(
        Visit.resident_id.in_(visible), Visit.status == "planned", Visit.due_date < today)
        .order_by(Visit.due_date).limit(20)).all()
    for v in overdue:
        items.append({"kind": "overdue_visit", "priority": PRIORITY["overdue_visit"], "ref_id": v.id,
                      "resident": names.get(v.resident_id, f"#{v.resident_id}"),
                      "label": f"随访逾期：原定 {v.due_date.isoformat()}"})

    pending_appts = db.scalars(select(Appointment).where(
        Appointment.resident_id.in_(visible), Appointment.status == "pending")
        .order_by(Appointment.created_at).limit(20)).all()
    for a in pending_appts:
        items.append({"kind": "pending_appointment", "priority": PRIORITY["pending_appointment"], "ref_id": a.id,
                      "resident": names.get(a.resident_id, f"#{a.resident_id}"),
                      "label": f"预约待确认：{a.preferred_date.isoformat()} {'上午' if a.time_slot == 'morning' else '下午'}"})

    claims = db.scalars(select(ClaimRequest).where(
        ClaimRequest.community_id == user.community_id, ClaimRequest.status == "pending")
        .order_by(ClaimRequest.created_at).limit(20)).all()
    for c in claims:
        items.append({"kind": "pending_claim", "priority": PRIORITY["pending_claim"], "ref_id": c.id,
                      "resident": c.stated_name,
                      "label": f"档案认领待核验：{c.stated_name}（档案 #{c.resident_id}）"})

    # Latest consent event per resident, pending review -> needs the peer reviewer.
    latest = (select(ConsentEvent.resident_id, func.max(ConsentEvent.id).label("eid"))
              .where(ConsentEvent.purpose == "community_care").group_by(ConsentEvent.resident_id).subquery())
    pending_consents = db.scalars(select(ConsentEvent)
                                  .join(latest, latest.c.eid == ConsentEvent.id)
                                  .where(ConsentEvent.resident_id.in_(visible),
                                         ConsentEvent.review_status == "pending")
                                  .order_by(ConsentEvent.created_at).limit(20)).all()
    for ev in pending_consents:
        items.append({"kind": "pending_consent", "priority": PRIORITY["pending_consent"], "ref_id": ev.id,
                      "resident": names.get(ev.resident_id, f"#{ev.resident_id}"),
                      "label": f"纸质同意待复核：{ev.evidence_ref or '无证据编号'}"})

    items.sort(key=lambda x: (x["priority"],))
    counts = {}
    for item in items:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    return {"items": items[:50], "total": len(items), "counts": counts,
            "generated_at": datetime.now(timezone.utc)}
