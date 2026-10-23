from datetime import datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import audit, auth_user, check_access
from ..models import Resident, User
from ..plan_models import HealthPlan, PlanOccurrence
from ..plan_schemas import PlanIn, SnoozeIn
from ..plan_worker import as_utc, generate_window

router = APIRouter(prefix="/api", tags=["health-plans"])


def access(db, user, resident_id, write=False):
    # Lock the resident on writes to serialize the per-resident plan limit.
    query = select(Resident).where(Resident.id == resident_id)
    if write:
        query = query.with_for_update()
    return check_access(db, user, db.scalar(query), scope="reminders", write=write)


def plan_out(plan):
    return {key: getattr(plan, key) for key in (
        "id", "resident_id", "title", "type", "frequency", "weekdays", "local_time",
        "timezone", "start_date", "end_date", "status", "created_by_id")}


def occurrence_out(row, plan):
    return {"id": row.id, "plan_id": row.plan_id, "resident_id": row.resident_id,
        "title": plan.title, "type": plan.type, "timezone": plan.timezone,
        "plan_status": plan.status, "scheduled_date": row.scheduled_date,
        "scheduled_at": as_utc(row.scheduled_at), "due_at": as_utc(row.due_at),
        "status": row.status, "cancellation_reason": row.cancellation_reason,
        "completed_at": as_utc(row.completed_at) if row.completed_at else None}


def get_plan(db, resident_id, plan_id):
    plan = db.scalar(select(HealthPlan).where(HealthPlan.id == plan_id,
        HealthPlan.resident_id == resident_id).with_for_update().execution_options(populate_existing=True))
    if not plan:
        raise HTTPException(404, "计划不存在")
    return plan


@router.get("/residents/{resident_id}/plans")
def list_plans(resident_id: int, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    access(db, user, resident_id)
    plans = db.scalars(select(HealthPlan).where(HealthPlan.resident_id == resident_id)
        .order_by(HealthPlan.id.desc()).limit(200)).all()
    for plan in plans:
        if plan.end_date and plan.end_date < datetime.now(ZoneInfo(plan.timezone)).date():
            plan.status = "ended"
    db.commit()
    return [plan_out(p) for p in plans]


@router.post("/residents/{resident_id}/plans", status_code=201)
def create_plan(resident_id: int, body: PlanIn, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    access(db, user, resident_id, write=True)
    total = db.scalar(select(func.count()).select_from(HealthPlan).where(HealthPlan.resident_id == resident_id))
    active = db.scalar(select(func.count()).select_from(HealthPlan).where(
        HealthPlan.resident_id == resident_id, HealthPlan.status != "ended"))
    if total >= 200 or active >= 50:
        raise HTTPException(409, "每位居民最多保留 200 个计划，同时使用的计划最多 50 个")
    plan = HealthPlan(resident_id=resident_id, created_by_id=user.id, **body.model_dump())
    db.add(plan)
    db.flush()
    generate_window(db, plan)
    audit(db, user, "create_health_plan", "health_plan", plan.id)
    db.commit()
    return plan_out(plan)


@router.post("/residents/{resident_id}/plans/{plan_id}/{action}")
def change_plan(resident_id: int, plan_id: int, action: Literal["pause", "resume", "end"],
                user: User = Depends(auth_user), db: Session = Depends(get_db)):
    access(db, user, resident_id, write=True)
    plan = get_plan(db, resident_id, plan_id)
    if plan.end_date and plan.end_date < datetime.now(ZoneInfo(plan.timezone)).date():
        plan.status = "ended"
    target = {"pause": "paused", "resume": "active", "end": "ended"}[action]
    if plan.status == "ended" and target != "ended":
        raise HTTPException(409, "已结束的计划不能恢复，请建立新计划")
    if plan.status == target:
        return plan_out(plan)
    now = datetime.now(timezone.utc)
    plan.status = target
    rows = db.scalars(select(PlanOccurrence).where(PlanOccurrence.plan_id == plan.id,
        PlanOccurrence.due_at > now).with_for_update()).all()
    for row in rows:
        if action in {"pause", "end"} and row.status == "pending":
            row.status = "cancelled"
            row.cancellation_reason = "plan_paused" if action == "pause" else "plan_ended"
        elif action == "resume" and row.status == "cancelled" and row.cancellation_reason == "plan_paused":
            row.status = "pending"
            row.cancellation_reason = None
    db.flush()
    if action == "resume":
        generate_window(db, plan, now=now)
    audit(db, user, action + "_health_plan", "health_plan", plan.id)
    db.commit()
    return plan_out(plan)


@router.get("/residents/{resident_id}/plan-occurrences")
def list_occurrences(resident_id: int, days: int = Query(default=7, ge=1, le=7),
                     status: Literal["pending", "completed", "cancelled", "all"] = "pending",
                     limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0),
                     user: User = Depends(auth_user), db: Session = Depends(get_db)):
    access(db, user, resident_id)
    now = datetime.now(timezone.utc)
    plans = db.scalars(select(HealthPlan).where(HealthPlan.resident_id == resident_id)
        .order_by(HealthPlan.id).limit(200)).all()
    windows = []
    for plan in plans:
        generate_window(db, plan, now=now, days=days)
        zone = ZoneInfo(plan.timezone)
        today = now.astimezone(zone).date()
        end = datetime.combine(today + timedelta(days=days), time(), tzinfo=zone).astimezone(timezone.utc)
        start = datetime.combine(today, time(), tzinfo=zone).astimezone(timezone.utc)
        windows.append(and_(PlanOccurrence.plan_id == plan.id, PlanOccurrence.due_at < end,
            or_(PlanOccurrence.status == "pending", PlanOccurrence.due_at >= start)))
    db.commit()
    if not windows:
        return {"items": [], "total": 0, "limit": limit, "offset": offset}
    query = select(PlanOccurrence).where(PlanOccurrence.resident_id == resident_id, or_(*windows))
    if status != "all":
        query = query.where(PlanOccurrence.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(PlanOccurrence.due_at, PlanOccurrence.id).offset(offset).limit(limit)).all()
    by_id = {p.id: p for p in plans}
    return {"items": [occurrence_out(row, by_id[row.plan_id]) for row in rows],
            "total": total, "limit": limit, "offset": offset}


@router.post("/residents/{resident_id}/plan-occurrences/{occurrence_id}/{action}")
def change_occurrence(resident_id: int, occurrence_id: int,
                      action: Literal["complete", "snooze", "cancel"], body: SnoozeIn | None = None,
                      user: User = Depends(auth_user), db: Session = Depends(get_db)):
    access(db, user, resident_id, write=True)
    row = db.scalar(select(PlanOccurrence).where(PlanOccurrence.id == occurrence_id,
        PlanOccurrence.resident_id == resident_id))
    if not row:
        raise HTTPException(404, "计划任务不存在")
    plan = get_plan(db, resident_id, row.plan_id)
    # Follow the generator/plan transition lock order: plan, then occurrence.
    row = db.scalar(select(PlanOccurrence).where(PlanOccurrence.id == occurrence_id)
        .with_for_update().execution_options(populate_existing=True))
    if (action == "complete" and row.status == "completed") or (action == "cancel" and row.status == "cancelled"):
        return occurrence_out(row, plan)
    if row.status != "pending":
        raise HTTPException(409, "任务已处理，请刷新列表")
    if action == "snooze":
        if body is None:
            raise HTTPException(422, "请提供带时区的延后时间")
        if body.due_at <= as_utc(row.due_at):
            raise HTTPException(422, "延后时间必须晚于当前任务时间")
        row.due_at = body.due_at
    elif action == "complete":
        row.status = "completed"
        row.completed_at = datetime.now(timezone.utc)
    else:
        row.status = "cancelled"
        row.cancellation_reason = "user_cancelled"
    row.acted_by_id = user.id
    audit(db, user, action + "_plan_task", "plan_occurrence", row.id)
    db.commit()
    return occurrence_out(row, plan)
