"""Bounded, idempotent local task generation; never sends notifications."""
import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .db import SessionLocal
from .plan_models import HealthPlan, PlanOccurrence

logger = logging.getLogger(__name__)


def as_utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def scheduled_instant(day, local_time, zone):
    """Skip nonexistent DST times; an ambiguous time occurs once, at fold=0."""
    hour, minute = map(int, local_time.split(":"))
    local = datetime.combine(day, time(hour, minute), tzinfo=zone)
    utc = local.astimezone(timezone.utc)
    if utc.astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
        return None
    return utc


def generate_window(db, plan, now=None, days=7):
    """Caller owns the transaction. Row locks serialize pause/end on PostgreSQL."""
    now = as_utc(now or datetime.now(timezone.utc))
    plan = db.scalar(select(HealthPlan).where(HealthPlan.id == plan.id)
                     .with_for_update().execution_options(populate_existing=True))
    if not plan:
        return 0
    zone = ZoneInfo(plan.timezone)
    today = now.astimezone(zone).date()
    if plan.end_date and plan.end_date < today and plan.status != "ended":
        plan.status = "ended"
        db.flush()
    if plan.status != "active":
        return 0
    # Deliberately do not invent occurrences for missed days while the app was off.
    first = max(today, plan.start_date)
    last = today + timedelta(days=min(max(days, 1), 7) - 1)
    if plan.end_date:
        last = min(last, plan.end_date)
    if first > last:
        return 0
    existing = {row.scheduled_date for row in db.scalars(select(PlanOccurrence).where(
        PlanOccurrence.plan_id == plan.id, PlanOccurrence.scheduled_date >= first,
        PlanOccurrence.scheduled_date <= last))}
    created = 0
    for index in range((last - first).days + 1):
        day = first + timedelta(days=index)
        if day in existing or (plan.frequency == "weekly" and day.weekday() not in plan.weekdays):
            continue
        instant = scheduled_instant(day, plan.local_time, zone)
        if instant is None:
            continue
        # A newly created plan should not immediately produce a task already overdue.
        if instant < as_utc(plan.created_at):
            continue
        try:
            with db.begin_nested():
                db.add(PlanOccurrence(plan_id=plan.id, resident_id=plan.resident_id,
                    scheduled_date=day, scheduled_at=instant, due_at=instant, status="pending"))
                db.flush()
            created += 1
        except IntegrityError:
            # A concurrent worker/list request may have inserted the same dated task.
            continue
    return created


def generate_all(now=None):
    """A small transaction per plan; keyset pages keep worker memory bounded."""
    cursor = 0
    while True:
        with SessionLocal() as db:
            ids = list(db.scalars(select(HealthPlan.id).where(
                HealthPlan.status == "active", HealthPlan.id > cursor).order_by(HealthPlan.id).limit(100)))
        if not ids:
            return
        for plan_id in ids:
            with SessionLocal() as db:
                plan = db.get(HealthPlan, plan_id)
                if plan:
                    generate_window(db, plan, now=now)
                    db.commit()
        cursor = ids[-1]


async def worker_loop(stop):
    while not stop.is_set():
        try:
            await asyncio.to_thread(generate_all)
        except Exception:
            # Avoid logging resident data or plan titles.
            logger.warning("Recurring task generation failed; retrying next minute")
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass


if __name__ == "__main__":
    asyncio.run(worker_loop(asyncio.Event()))
