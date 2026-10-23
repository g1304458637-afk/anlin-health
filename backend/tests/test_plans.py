"""Recurring plans: local dates, deterministic generation, state and permissions."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db import get_db
from app.models import ConsentEvent, Resident, User
from app.plan_models import HealthPlan, PlanOccurrence
from app.plan_worker import as_utc, generate_window, scheduled_instant
from app.routes.plans import router
from app.security import access_token


@pytest.fixture
def plan_client(db):
    app = FastAPI()
    app.include_router(router)
    def session():
        yield db
    app.dependency_overrides[get_db] = session
    with TestClient(app) as client:
        yield client


def headers(db, name="resident"):
    user = db.scalar(select(User).where(User.username == name))
    return {"Authorization": "Bearer " + access_token(user.id, user.token_version)}


def resident_id(db):
    return db.scalar(select(Resident.id).where(Resident.owner_user_id.is_not(None)))


def payload(**changes):
    result = {"title": "虚构日常安排", "type": "measure", "frequency": "daily", "weekdays": [],
              "local_time": "08:00", "timezone": "Asia/Shanghai",
              "start_date": (datetime.now(ZoneInfo("Asia/Shanghai")).date() + timedelta(days=1)).isoformat()}
    return result | changes


def new_plan(db, **changes):
    now = datetime(2026, 1, 30, 0, tzinfo=timezone.utc)
    values = dict(resident_id=resident_id(db), created_by_id=db.scalar(select(User.id).where(User.username == "resident")),
        title="测试计划", type="measure", frequency="daily", weekdays=[], local_time="08:00",
        timezone="Asia/Shanghai", start_date=date(2026, 1, 30), end_date=date(2026, 2, 3),
        status="active", created_at=now-timedelta(days=1))
    plan = HealthPlan(**(values | changes)); db.add(plan); db.flush()
    return plan


def test_generation_crosses_month_once_and_honors_end_date(db):
    plan = new_plan(db)
    now = datetime(2026, 1, 30, tzinfo=timezone.utc)
    assert generate_window(db, plan, now=now) == 5
    assert generate_window(db, plan, now=now) == 0
    rows = db.scalars(select(PlanOccurrence).order_by(PlanOccurrence.scheduled_at)).all()
    assert [r.scheduled_date for r in rows] == [date(2026, 1, 30)+timedelta(days=i) for i in range(5)]
    assert as_utc(rows[0].scheduled_at) == now
    assert generate_window(db, plan, now=datetime(2026, 2, 4, tzinfo=timezone.utc)) == 0
    assert plan.status == "ended"


def test_weekdays_timezone_and_dst_are_deterministic(db):
    plan = new_plan(db, frequency="weekly", weekdays=[0, 6], end_date=None)
    assert generate_window(db, plan, now=datetime(2026, 1, 30, tzinfo=timezone.utc)) == 2
    assert [r.scheduled_date for r in db.scalars(select(PlanOccurrence).order_by(PlanOccurrence.scheduled_date))] == [date(2026, 2, 1), date(2026, 2, 2)]
    ny = ZoneInfo("America/New_York")
    assert scheduled_instant(date(2026, 3, 8), "02:30", ny) is None
    assert scheduled_instant(date(2026, 11, 1), "01:30", ny) == datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)


def test_database_rejects_duplicate_occurrence(db):
    plan = new_plan(db); generate_window(db, plan, now=datetime(2026, 1, 30, tzinfo=timezone.utc))
    row = db.scalar(select(PlanOccurrence))
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(PlanOccurrence(plan_id=plan.id, resident_id=plan.resident_id, scheduled_date=row.scheduled_date,
            scheduled_at=row.scheduled_at, due_at=row.due_at)); db.flush()


def test_api_create_list_and_actions_are_idempotent(plan_client, db):
    rid = resident_id(db); auth = headers(db); base = f"/api/residents/{rid}"
    response = plan_client.post(base+"/plans", json=payload(), headers=auth)
    assert response.status_code == 201, response.text
    pid = response.json()["id"]
    first = plan_client.get(base+"/plan-occurrences", headers=auth).json()
    assert first["total"] == 6
    assert plan_client.get(base+"/plan-occurrences", headers=auth).json()["total"] == 6
    task = first["items"][0]
    path = base+f"/plan-occurrences/{task['id']}"
    assert plan_client.post(path+"/complete", headers=auth).json()["status"] == "completed"
    assert plan_client.post(path+"/complete", headers=auth).status_code == 200
    assert plan_client.post(path+"/cancel", headers=auth).status_code == 409
    assert plan_client.post(base+f"/plans/{pid}/end", headers=auth).json()["status"] == "ended"
    assert plan_client.post(base+f"/plans/{pid}/resume", headers=auth).status_code == 409


def test_pause_keeps_overdue_and_resume_does_not_revive_manual_cancellations(plan_client, db):
    rid = resident_id(db); auth = headers(db); base = f"/api/residents/{rid}"
    pid = plan_client.post(base+"/plans", json=payload(), headers=auth).json()["id"]
    rows = db.scalars(select(PlanOccurrence).where(PlanOccurrence.plan_id == pid).order_by(PlanOccurrence.id)).all()
    rows[0].due_at = datetime.now(timezone.utc)-timedelta(hours=1); db.commit()
    plan_client.post(base+f"/plan-occurrences/{rows[1].id}/cancel", headers=auth)
    assert plan_client.post(base+f"/plans/{pid}/pause", headers=auth).status_code == 200
    db.expire_all()
    assert db.get(PlanOccurrence, rows[0].id).status == "pending"
    assert db.get(PlanOccurrence, rows[2].id).cancellation_reason == "plan_paused"
    assert plan_client.get(base+"/plan-occurrences", headers=auth).json()["total"] == 1
    assert plan_client.post(base+f"/plans/{pid}/resume", headers=auth).status_code == 200
    db.expire_all()
    assert db.get(PlanOccurrence, rows[1].id).cancellation_reason == "user_cancelled"
    assert db.get(PlanOccurrence, rows[2].id).status == "pending"


def test_snooze_requires_timezone_future_and_later_than_current(plan_client, db):
    rid=resident_id(db); auth=headers(db); base=f"/api/residents/{rid}"
    plan_client.post(base+"/plans",json=payload(),headers=auth)
    row=plan_client.get(base+"/plan-occurrences",headers=auth).json()["items"][0]
    path=base+f"/plan-occurrences/{row['id']}/snooze"
    due=datetime.fromisoformat(row["due_at"])
    assert plan_client.post(path,json={"due_at":(due+timedelta(hours=1)).replace(tzinfo=None).isoformat()},headers=auth).status_code==422
    assert plan_client.post(path,json={"due_at":(due-timedelta(hours=1)).isoformat()},headers=auth).status_code==422
    response=plan_client.post(path,json={"due_at":(due+timedelta(hours=1)).isoformat()},headers=auth)
    assert response.status_code==200
    assert response.json()["scheduled_at"]==row["scheduled_at"]


def test_family_admin_cross_community_and_withdrawn_staff_are_denied(plan_client, db):
    rid=resident_id(db); base=f"/api/residents/{rid}"
    for username in ("family","admin","staff2","unlinked"):
        auth=headers(db,username)
        assert plan_client.get(base+"/plans",headers=auth).status_code==404
        assert plan_client.post(base+"/plans",json=payload(),headers=auth).status_code==404
    staff=headers(db,"staff")
    assert plan_client.post(base+"/plans",json=payload(),headers=staff).status_code==201
    owner=db.scalar(select(User).where(User.username=="resident"))
    db.add(ConsentEvent(resident_id=rid,actor_id=owner.id,granted=False,method="resident_self"));db.commit()
    assert plan_client.get(base+"/plan-occurrences",headers=staff).status_code==404
    assert plan_client.get(base+"/plans",headers=headers(db)).status_code==200


@pytest.mark.parametrize("change",[{"frequency":"weekly","weekdays":[]},{"weekdays":[1]},
    {"frequency":"weekly","weekdays":[7]},{"frequency":"weekly","weekdays":[1,1]},
    {"timezone":"Not/AZone"},{"local_time":"25:00"},{"title":"   "}])
def test_invalid_schedules_rejected(plan_client,db,change):
    assert plan_client.post(f"/api/residents/{resident_id(db)}/plans",json=payload(**change),headers=headers(db)).status_code==422


def test_plan_limit_is_enforced(plan_client,db):
    rid=resident_id(db)
    for index in range(50):
        new_plan(db,start_date=date.today(),end_date=None,title=f"计划{index}")
    db.commit()
    assert plan_client.post(f"/api/residents/{rid}/plans",json=payload(),headers=headers(db)).status_code==409
