from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from ..db import get_db
from ..models import User, Resident, Community, ConsentEvent
from ..deps import auth_user, require_roles, check_access, is_owner, is_staff_for, consent_active, audit
from ..schemas import ResidentOut, SelfResidentIn, AssistedResidentIn, ResidentPatch, ConsentIn, ConsentOut

router = APIRouter(prefix="/api", tags=["residents"])


def serialize(db: Session, resident: Resident, user: User) -> ResidentOut:
    # Family should never see a phone number if granted vitals-only access.
    return ResidentOut(
        id=resident.id, name=resident.name, birth_year=resident.birth_year,
        sex=resident.sex, phone=resident.phone, community_id=resident.community_id,
        owner_user_id=resident.owner_user_id, consent_active=consent_active(db, resident.id)
    )


@router.get("/communities")
def communities(user: User = Depends(auth_user), db: Session = Depends(get_db)):
    return [{"id": c.id, "name": c.name} for c in db.scalars(select(Community).order_by(Community.id)).all()]


@router.get("/residents/me", response_model=ResidentOut)
def my_resident(user: User = Depends(auth_user), db: Session = Depends(get_db)):
    resident = db.scalar(select(Resident).where(Resident.owner_user_id == user.id))
    return serialize(db, check_access(db, user, resident), user)


@router.post("/residents/me", response_model=ResidentOut, status_code=201)
def create_my_resident(body: SelfResidentIn, user: User = Depends(require_roles("resident")), db: Session = Depends(get_db)):
    if not body.consent_to_community_care:
        raise HTTPException(422, "需要明确同意社区健康信息处理说明")
    if body.birth_year > datetime.now().year:
        raise HTTPException(422, "出生年份不能大于当前年份")
    if db.scalar(select(Resident).where(Resident.owner_user_id == user.id)):
        raise HTTPException(409, "已创建健康档案")
    if not db.get(Community, body.community_id):
        raise HTTPException(404, "社区不存在")
    resident = Resident(owner_user_id=user.id, created_by_id=user.id, **body.model_dump(exclude={"consent_to_community_care"}))
    db.add(resident)
    db.flush()
    db.add(ConsentEvent(resident_id=resident.id, actor_id=user.id, purpose="community_care", granted=True, method="resident_self"))
    audit(db, user, "create_self_resident", "resident", resident.id)
    db.commit()
    return serialize(db, resident, user)


@router.get("/community/residents", response_model=list[ResidentOut])
def list_community_residents(q: str = Query(default="", max_length=80), limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0), user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    query = select(Resident).where(Resident.community_id == user.community_id)
    if q.strip():
        safe_q = q.strip().replace("%", r"\%").replace("_", r"\_")
        query = query.where(Resident.name.ilike(f"%{safe_q}%", escape="\\"))
    items = db.scalars(query.order_by(Resident.id.desc()).offset(offset).limit(limit)).all()
    # Processing consent is necessary before presenting residents' health information.
    return [serialize(db, r, user) for r in items if consent_active(db, r.id)]


@router.post("/community/residents", response_model=ResidentOut, status_code=201)
def create_assisted_resident(body: AssistedResidentIn, user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    if body.community_id != user.community_id:
        raise HTTPException(403, "不能向其他社区录入居民")
    if not body.consent_confirmed:
        raise HTTPException(422, "线下未获得知情同意，不允许录入健康档案")
    if body.birth_year > datetime.now().year:
        raise HTTPException(422, "出生年份不能大于当前年份")
    resident = Resident(created_by_id=user.id, **body.model_dump(exclude={"consent_confirmed", "evidence_ref"}))
    db.add(resident)
    db.flush()
    db.add(ConsentEvent(resident_id=resident.id, actor_id=user.id, purpose="community_care", granted=True, method="paper_assisted", evidence_ref=body.evidence_ref))
    audit(db, user, "create_assisted_resident", "resident", resident.id)
    db.commit()
    return serialize(db, resident, user)


@router.get("/residents/{resident_id}", response_model=ResidentOut)
def get_resident(resident_id: int, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    resident = check_access(db, user, db.get(Resident, resident_id), scope="profile")
    return serialize(db, resident, user)


@router.patch("/residents/{resident_id}", response_model=ResidentOut)
def update_resident(resident_id: int, body: ResidentPatch, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    resident = check_access(db, user, db.get(Resident, resident_id), write=True)
    data = body.model_dump(exclude_unset=True)
    if "birth_year" in data and data["birth_year"] is not None and data["birth_year"] > datetime.now().year:
        raise HTTPException(422, "出生年份无效")
    for key, value in data.items():
        if key not in {"name", "birth_year", "sex", "phone"}:
            raise HTTPException(422, "不可修改此字段")
        setattr(resident, key, value)
    audit(db, user, "update_resident", "resident", resident.id)
    db.commit()
    return serialize(db, resident, user)


@router.get("/residents/{resident_id}/consents", response_model=list[ConsentOut])
def consents(resident_id: int, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    resident = db.get(Resident, resident_id)
    if not resident or not (is_owner(user, resident) or is_staff_for(user, resident)):
        raise HTTPException(404, "档案不存在")
    return db.scalars(select(ConsentEvent).where(ConsentEvent.resident_id == resident_id).order_by(ConsentEvent.id.desc())).all()


@router.post("/residents/{resident_id}/consents", response_model=ConsentOut, status_code=201)
def update_consent(resident_id: int, body: ConsentIn, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    resident = db.get(Resident, resident_id)
    if resident is None or not (is_owner(user, resident) or is_staff_for(user, resident)):
        raise HTTPException(404, "档案不存在")
    if is_staff_for(user, resident):
        # Staff must reference documentary evidence for both consent and withdrawal.
        if not body.evidence_ref or len(body.evidence_ref) < 5:
            raise HTTPException(422, "工作人员需要登记知情同意或撤回的证据编号")
        method = "paper_assisted"
    else:
        method = "resident_self"
    event = ConsentEvent(resident_id=resident_id, actor_id=user.id, purpose="community_care", granted=body.granted, method=method, evidence_ref=body.evidence_ref if method == "paper_assisted" else None)
    db.add(event)
    db.flush()
    audit(db, user, "grant_care_consent" if body.granted else "withdraw_care_consent", "resident", resident_id)
    db.commit()
    return event


@router.get("/community/stats")
def community_stats(user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    ids = list(db.scalars(select(Resident.id).where(Resident.community_id == user.community_id)))
    # Aggregate only. Never include questionnaire raw answers in dashboard.
    active = [rid for rid in ids if consent_active(db, rid)]
    return {"resident_count": len(active), "community_id": user.community_id}
