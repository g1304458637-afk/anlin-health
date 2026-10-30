"""Risk notice surfaces: resident/family per-resident history and the staff console queue."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import audit, auth_user, check_access, consent_active, require_roles
from ..models import Resident, RiskAlert, User, Visit
from ..reporting import visible_resident_ids

router = APIRouter(prefix="/api", tags=["risk alerts"])

OUTCOMES = {"called": "电话慰问", "visited": "上门探访", "referred": "建议就医", "no_action": "暂无需处理"}


class RiskAckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str = Field(pattern="^(called|visited|referred|no_action)$")
    outcome_note: str | None = Field(default=None, max_length=300)


def _out(alert: RiskAlert, resident: Resident | None) -> dict:
    return {"id": alert.id, "resident_id": alert.resident_id,
            "resident_name": resident.name if resident else None,
            "vital_id": alert.vital_id, "metric": alert.metric, "level": alert.level,
            "value_text": alert.value_text, "message": alert.message,
            "acknowledged_at": alert.acknowledged_at,
            "outcome": alert.outcome, "outcome_label": OUTCOMES.get(alert.outcome),
            "outcome_note": alert.outcome_note,
            "created_at": alert.created_at}


@router.get("/residents/{resident_id}/risk-alerts")
def resident_risk_alerts(resident_id: int, response: Response,
                         days: int = Query(default=30, ge=1, le=90),
                         user: User = Depends(auth_user), db: Session = Depends(get_db)):
    """Alert history for one resident. The resident themself, staff with valid
    consent, and family with an active vitals grant (via check_access scope)."""
    response.headers["Cache-Control"] = "no-store"
    check_access(db, user, db.get(Resident, resident_id), scope="vitals")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.scalars(select(RiskAlert).where(RiskAlert.resident_id == resident_id,
                                              RiskAlert.created_at >= cutoff)
                      .order_by(RiskAlert.created_at.desc(), RiskAlert.id.desc()).limit(100)).all()
    return {"items": [_out(a, db.get(Resident, resident_id)) for a in rows]}


@router.get("/community/risk-alerts")
def community_risk_alerts(response: Response, level: str | None = Query(None, pattern="^(warning|critical)$"),
                          status: str = Query(default="open", pattern="^(open|acknowledged|all)$"),
                          limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
                          user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    """Console queue: alerts for this community's residents with valid consent."""
    response.headers["Cache-Control"] = "no-store"
    visible = visible_resident_ids(user.community_id)
    query = select(RiskAlert).where(RiskAlert.resident_id.in_(visible))
    if level:
        query = query.where(RiskAlert.level == level)
    if status == "open":
        query = query.where(RiskAlert.acknowledged_at.is_(None))
    elif status == "acknowledged":
        query = query.where(RiskAlert.acknowledged_at.is_not(None))
    rows = db.scalars(query.order_by(RiskAlert.acknowledged_at.is_not(None),
                                     RiskAlert.level.is_("warning"),  # critical first
                                     RiskAlert.created_at.desc(), RiskAlert.id.desc())
                      .offset(offset).limit(limit)).all()
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    open_count = db.scalar(select(func.count()).select_from(
        select(RiskAlert).where(RiskAlert.resident_id.in_(visible), RiskAlert.acknowledged_at.is_(None)).subquery())) or 0
    return {"items": [_out(a, db.get(Resident, a.resident_id)) for a in rows],
            "total": total, "open_count": open_count, "limit": limit, "offset": offset}


def _open_alert_for_staff(db: Session, alert_id: int, user: User) -> RiskAlert:
    alert = db.get(RiskAlert, alert_id)
    resident = db.get(Resident, alert.resident_id) if alert else None
    if not alert or not resident or resident.community_id != user.community_id:
        raise HTTPException(404, "预警不存在")
    return alert


@router.post("/community/risk-alerts/{alert_id}/ack")
def acknowledge_alert(alert_id: int, body: RiskAckIn,
                      user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    """Close the loop: record how staff responded, not just that they looked."""
    alert = _open_alert_for_staff(db, alert_id, user)
    if alert.acknowledged_at is None:
        alert.acknowledged_at = datetime.now(timezone.utc)
        alert.acknowledged_by_id = user.id
        alert.outcome = body.outcome
        alert.outcome_note = body.outcome_note
        audit(db, user, "risk_alert_acknowledged", "risk_alert", alert.id,
              {"outcome": body.outcome})
        db.commit()
    return {"ok": True, "outcome_label": OUTCOMES.get(alert.outcome)}


@router.post("/community/risk-alerts/{alert_id}/to-visit", status_code=201)
def alert_to_visit(alert_id: int, user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    """One click: turn an open alert into a planned home-visit task for the resident."""
    alert = _open_alert_for_staff(db, alert_id, user)
    if alert.acknowledged_at is not None:
        raise HTTPException(409, "该预警已处理，可直接在入户随访中新建任务")
    # The task note carries health values; creating it requires valid consent,
    # same as every other staff write on the resident's record.
    if not consent_active(db, alert.resident_id):
        raise HTTPException(409, "该居民的社区服务授权未生效，暂不能生成随访任务")
    existing = db.scalar(select(Visit.id).where(
        Visit.resident_id == alert.resident_id, Visit.status == "planned", Visit.staff_id == user.id,
        Visit.note.like(f"[风险预警 #{alert.id}]%")))
    if existing:
        raise HTTPException(409, "该预警已生成过随访任务")
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    visit = Visit(resident_id=alert.resident_id, staff_id=user.id, due_date=tomorrow, status="planned",
                  note=f"[风险预警 #{alert.id}] {alert.metric} {alert.value_text} —— 上门复测并了解情况")
    db.add(visit)
    db.flush()
    audit(db, user, "risk_alert_to_visit", "visit", visit.id, {"alert_id": alert.id})
    db.commit()
    return {"visit_id": visit.id, "due_date": visit.due_date.isoformat()}
