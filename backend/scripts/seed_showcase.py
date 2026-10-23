"""Additional fictional records for the opt-in localhost preview only."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select, func

from app.config import settings
from app.db import SessionLocal
from app.models import User, Resident, Vital, FamilyGrant
from app.seed import seed
from app.plan_models import HealthPlan
from app.plan_worker import generate_window


def seed_showcase():
    if settings.app_env != "development" or not settings.demo_auth:
        raise RuntimeError("Showcase data require development mode and explicit demo authentication")
    seed()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "resident"))
        resident = db.scalar(select(Resident).where(Resident.owner_user_id == user.id)) if user else None
        if not resident or resident.name not in {"王静（虚构）", "虚构王一"}:
            raise RuntimeError("This is not the dedicated fictional demonstration database")
        resident.name = "虚构王一"
        now = datetime.now(timezone.utc)
        if db.scalar(select(func.count()).select_from(Vital).where(Vital.resident_id == resident.id)) <= 3:
            for offset in (29, 27, 25, 23, 20, 17, 15, 13, 11, 9, 7, 4, 2, 0):
                at = (now - timedelta(days=offset)).replace(hour=0, minute=15, second=0, microsecond=0)
                if at > now:
                    at = now - timedelta(minutes=10)
                db.add(Vital(resident_id=resident.id,actor_id=user.id,type="blood_pressure",
                    systolic=121+(offset*7)%12,diastolic=75+(offset*3)%9,measured_at=at))
                db.add(Vital(resident_id=resident.id,actor_id=user.id,type="blood_glucose",
                    glucose_mmol_l=round(5.1+(offset%6)*.15,1),measurement_context="fasting",measured_at=at))
                db.add(Vital(resident_id=resident.id,actor_id=user.id,type="weight",
                    weight_kg=round(61.4+(offset%8)*.1,1),measured_at=at))
        if not db.scalar(select(HealthPlan.id).where(HealthPlan.resident_id == resident.id).limit(1)):
            today = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
            plans = [HealthPlan(resident_id=resident.id,created_by_id=user.id,title="早晨记录一次血压",type="measure",
                frequency="daily",weekdays=[],local_time="08:00",timezone="Asia/Shanghai",start_date=today),
                HealthPlan(resident_id=resident.id,created_by_id=user.id,title="每周整理健康记录",type="general",
                frequency="weekly",weekdays=[4],local_time="18:00",timezone="Asia/Shanghai",start_date=today)]
            db.add_all(plans)
            db.flush()
            for plan in plans:
                generate_window(db, plan, now=now)
        family = db.scalar(select(User).where(User.username == "family"))
        if family and not db.scalar(select(FamilyGrant.id).where(FamilyGrant.resident_id==resident.id,FamilyGrant.grantee_id==family.id)):
            db.add(FamilyGrant(resident_id=resident.id,grantee_id=family.id,can_profile=True,can_vitals=True,expires_at=now+timedelta(days=30)))
        db.commit()
        from scripts.showcase_reports import seed_showcase_reports
        seed_showcase_reports(db,resident,user)
        db.commit()


if __name__ == "__main__":
    seed_showcase()
