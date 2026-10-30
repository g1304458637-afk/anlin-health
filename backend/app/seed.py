"""Development/demo data only. All profiles are fictional. Never run on production data."""
from datetime import datetime, timedelta, timezone, date
from sqlalchemy import select
from .db import SessionLocal, Base, engine
from .config import settings
from .models import User, Community, Resident, ConsentEvent, Vital, Reminder, Questionnaire, Visit, RiskAlert, Appointment, FamilyGrant
from .security import hash_password, new_invite_code

SAMPLE_QS = [
    {"slug": "daily-health", "title": "日常健康习惯自评（示例）", "category": "general", "questions": [
        {"id": "activity", "text": "过去7天，您通常每天活动多久？", "options": [{"value": "under10", "label": "少于10分钟"}, {"value": "10to30", "label": "10至30分钟"}, {"value": "over30", "label": "超过30分钟"}]},
        {"id": "sleep", "text": "最近一周，您的睡眠感受如何？", "options": [{"value": "good", "label": "总体较好"}, {"value": "average", "label": "一般"}, {"value": "poor", "label": "不太好"}]},
        {"id": "help", "text": "您是否愿意接收社区健康知识？", "options": [{"value": "yes", "label": "愿意"}, {"value": "no", "label": "暂不需要"}]},
    ]},
    {"slug": "mood-checkin", "title": "情绪与生活状态记录（非临床量表）", "category": "wellbeing", "questions": [
        {"id": "mood", "text": "过去7天，您对自己整体心情的感受是？", "options": [{"value": "positive", "label": "比较积极"}, {"value": "mixed", "label": "有好有坏"}, {"value": "difficult", "label": "比较低落"}]},
        {"id": "support", "text": "当您需要帮助时，是否有可以联系的人？", "options": [{"value": "yes", "label": "有"}, {"value": "unsure", "label": "不确定"}, {"value": "no", "label": "没有"}]},
    ]}
]


def seed():
    if settings.app_env == "production":
        raise RuntimeError("Seed data are strictly forbidden in production")
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if db.scalar(select(User.id).limit(1)):
            print("Database has existing accounts; seed skipped")
            return
        c1, c2 = Community(name="春和社区（演示）"), Community(name="梅园社区（演示）")
        db.add_all([c1, c2]); db.flush()
        # Credentials exist for localhost demonstrations only.
        demo_pass = hash_password("DemoPass2026!")
        staff = User(username="staff", display_name="社区工作者·李老师", role="staff", community_id=c1.id, password_hash=demo_pass, invite_code=new_invite_code())
        staff2 = User(username="staff2", display_name="其他社区工作者", role="staff", community_id=c2.id, password_hash=demo_pass, invite_code=new_invite_code())
        resident_user = User(username="resident", display_name="居民·王阿姨", role="resident", password_hash=demo_pass, invite_code=new_invite_code())
        family_user = User(username="family", display_name="家属·小王", role="family", password_hash=demo_pass, invite_code=new_invite_code())
        admin = User(username="admin", display_name="系统管理员", role="admin", password_hash=demo_pass, invite_code=new_invite_code())
        db.add_all([staff, staff2, resident_user, family_user, admin]); db.flush()
        r1 = Resident(name="王静（虚构）", birth_year=1957, sex="female", community_id=c1.id, owner_user_id=resident_user.id, created_by_id=resident_user.id)
        r2 = Resident(name="陈平（虚构）", birth_year=1950, sex="male", community_id=c1.id, created_by_id=staff.id)
        r3 = Resident(name="赵桂芳（虚构）", birth_year=1949, sex="female", community_id=c1.id, created_by_id=staff.id)
        r4 = Resident(name="刘明（虚构）", birth_year=1961, sex="male", community_id=c2.id, created_by_id=staff2.id)
        db.add_all([r1,r2,r3,r4]); db.flush()
        for r, u in [(r1,resident_user),(r2,staff),(r3,staff2),(r4,staff2)]:
            db.add(ConsentEvent(resident_id=r.id, actor_id=u.id, purpose="community_care", granted=True,
                method="resident_self" if r.id == r1.id else "paper_assisted", evidence_ref=None if r.id == r1.id else "DEMO-CONSENT-001"))
        for days, systolic, diastolic in [(5,129,82),(3,125,80),(1,127,79)]:
            db.add(Vital(resident_id=r1.id, actor_id=resident_user.id, type="blood_pressure", systolic=systolic,
                diastolic=diastolic, measured_at=datetime.now(timezone.utc)-timedelta(days=days)))
        db.add(Vital(resident_id=r2.id, actor_id=staff.id, type="blood_glucose", glucose_mmol_l=5.8))
        db.add(FamilyGrant(resident_id=r1.id, grantee_id=family_user.id, can_profile=True, can_vitals=True,
            expires_at=datetime.now(timezone.utc)+timedelta(days=365)))
        db.add(Reminder(resident_id=r1.id, actor_id=resident_user.id, type="measure", title="记录今天的血压", due_date=date.today()))
        db.add(Reminder(resident_id=r1.id, actor_id=resident_user.id, type="followup", title="看看预约是否有社区回复", due_date=date.today()+timedelta(days=1)))
        db.add(Visit(resident_id=r2.id, staff_id=staff.id, due_date=date.today()+timedelta(days=1), status="planned"))
        for template in SAMPLE_QS:
            db.add(Questionnaire(version=1, published=True, **template))
        # Demo surfaces for the risk-notice queue and the appointment board.
        high = Vital(resident_id=r1.id, actor_id=resident_user.id, type="blood_pressure",
                     systolic=168, diastolic=102,
                     measured_at=datetime.now(timezone.utc)-timedelta(hours=3))
        db.add(high); db.flush()
        db.add(RiskAlert(resident_id=r1.id, vital_id=high.id, metric="blood_pressure", level="critical",
            value_text="168/102 mmHg",
            message="本次血压 168/102 mmHg 高于常见关注范围（≥140/90），建议按平时习惯复测并留意变化。本提示来自常见参考范围，不构成医疗诊断。"))
        db.add(Appointment(resident_id=r1.id, requester_id=resident_user.id, kind="doctor",
            preferred_date=date.today()+timedelta(days=2), time_slot="morning",
            note="想请人陪同去社区卫生服务中心复诊", status="pending"))
        db.commit()
    print("Demo data seeded. Accounts: staff/staff2/resident/family/admin; password: DemoPass2026!")

if __name__ == "__main__":
    seed()
