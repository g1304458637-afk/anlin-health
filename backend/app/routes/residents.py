from datetime import datetime, timezone
import re
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from ..db import get_db
from ..config import settings
from ..models import User, Resident, Community, ConsentEvent, ClaimRequest
from ..deps import auth_user, require_roles, check_access, is_owner, is_staff_for, consent_active, audit
from ..ratelimit import claim_limiter, provision_limiter, client_ip
from ..security import validate_password_strength, hash_password, new_invite_code
from ..schemas import (ResidentOut, SelfResidentIn, AssistedResidentIn, ResidentPatch, ConsentIn, ConsentOut,
                       ConsentReviewIn, ClaimRequestIn, ClaimRequestOut, ClaimReviewIn, ProvisionAccountIn)

router = APIRouter(prefix="/api", tags=["residents"])

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fa5]{4,40}$")  # 中文用户名对老年居民更友好


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
    # Paper-assisted consent starts as pending review; a reviewer rejection cuts access.
    db.add(ConsentEvent(resident_id=resident.id, actor_id=user.id, purpose="community_care", granted=True,
                        method="paper_assisted", evidence_ref=body.evidence_ref, review_status="pending"))
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
    review_status = "pending" if method == "paper_assisted" else "approved"
    event = ConsentEvent(resident_id=resident_id, actor_id=user.id, purpose="community_care", granted=body.granted,
                         method=method, evidence_ref=body.evidence_ref if method == "paper_assisted" else None,
                         review_status=review_status)
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


# ---- 居民账号开通（网页端） -----------------------------------------------
# 工作人员为本社区老人开通登录账号；居民数据访问边界不因此改变。

@router.post("/community/accounts", status_code=201)
def provision_account(body: ProvisionAccountIn, user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    if not user.community_id:
        raise HTTPException(403, "账号未关联社区，不能开通居民账号")
    if not provision_limiter.hit(f"provision:{user.id}", 30, window_seconds=3600):
        raise HTTPException(429, "开通过于频繁，请稍后再试")
    if not USERNAME_RE.fullmatch(body.username):
        raise HTTPException(422, "用户名需为4-40位字母、数字或_.-")
    if db.scalar(select(User.id).where(User.username == body.username)):
        raise HTTPException(409, "用户名已被使用")
    reason = validate_password_strength(body.initial_password)
    if reason:
        raise HTTPException(422, reason)
    account = User(username=body.username, display_name=body.display_name, role="resident",
                   community_id=user.community_id, invite_code=new_invite_code(),
                   password_hash=hash_password(body.initial_password))
    db.add(account)
    db.flush()
    audit(db, user, "provision_resident_account", "user", account.id)
    db.commit()
    return {"id": account.id, "username": account.username, "display_name": account.display_name,
            "role": account.role, "community_id": account.community_id}


# ---- Offline archive claiming -------------------------------------------------
# Name + phone alone NEVER transfer ownership: matching submissions only open a
# request that community staff must approve after an in-person identity check.

@router.post("/residents/claim-requests", status_code=202)
def create_claim_request(body: ClaimRequestIn, request: Request, user: User = Depends(require_roles("resident")), db: Session = Depends(get_db)):
    if db.scalar(select(Resident.id).where(Resident.owner_user_id == user.id)):
        raise HTTPException(409, "已有居民档案，无需认领")
    if not claim_limiter.hit(f"claim:{user.id}", 5, window_seconds=3600):
        raise HTTPException(429, "认领申请过于频繁，请稍后再试")
    resident = db.get(Resident, body.resident_id)
    # Anti-enumeration: the response is identical whether or not details match.
    generic = {"status": "submitted", "message": "申请已提交，请携带有效证件到社区现场核验"}
    if resident is None:
        return generic
    existing = db.scalar(select(ClaimRequest).where(
        ClaimRequest.resident_id == resident.id, ClaimRequest.requester_id == user.id,
        ClaimRequest.status == "pending"))
    if existing:
        return generic
    matched = resident.name == body.full_name.strip() and bool(body.phone) and resident.phone == body.phone
    if matched:
        db.add(ClaimRequest(resident_id=resident.id, requester_id=user.id, community_id=resident.community_id,
                            stated_name=body.full_name.strip(), stated_phone=body.phone,
                            name_matched=True, phone_matched=True, note=body.note))
        audit(db, user, "create_claim_request", "resident", resident.id)
        db.commit()
    return generic


def _claim_out(db: Session, c: ClaimRequest) -> ClaimRequestOut:
    requester = db.get(User, c.requester_id)
    return ClaimRequestOut(id=c.id, resident_id=c.resident_id, requester_id=c.requester_id,
                           requester_name=requester.display_name if requester else "已停用账号",
                           status=c.status, stated_name=c.stated_name, stated_phone=c.stated_phone,
                           name_matched=c.name_matched, phone_matched=c.phone_matched,
                           note=c.note, reviewed_at=c.reviewed_at, created_at=c.created_at)


@router.get("/community/claim-requests", response_model=list[ClaimRequestOut])
def list_claim_requests(status: str = Query(default="pending", pattern="^(pending|approved|rejected)$"),
                        user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    rows = db.scalars(select(ClaimRequest).join(Resident, ClaimRequest.resident_id == Resident.id)
                      .where(Resident.community_id == user.community_id, ClaimRequest.status == status)
                      .order_by(ClaimRequest.id.desc()).limit(100)).all()
    return [_claim_out(db, c) for c in rows]


@router.post("/community/claim-requests/{claim_id}/review", response_model=ClaimRequestOut)
def review_claim_request(claim_id: int, body: ClaimReviewIn, user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    claim = db.get(ClaimRequest, claim_id)
    if not claim:
        raise HTTPException(404, "申请不存在")
    resident = db.get(Resident, claim.resident_id)
    if not resident or resident.community_id != user.community_id:
        raise HTTPException(404, "申请不存在")
    if claim.status != "pending":
        raise HTTPException(409, "该申请已处理")
    claim.status = "approved" if body.approve else "rejected"
    claim.reviewed_by_id = user.id
    claim.reviewed_at = datetime.now(timezone.utc)
    claim.note = body.note
    if body.approve:
        if resident.owner_user_id is not None:
            raise HTTPException(409, "该档案已被认领")
        owned = db.scalar(select(Resident.id).where(Resident.owner_user_id == claim.requester_id, Resident.id != resident.id))
        if owned:
            raise HTTPException(409, "申请人已有其他档案，请拒绝该申请")
        resident.owner_user_id = claim.requester_id
    audit(db, user, "approve_claim" if body.approve else "reject_claim", "claim_request", claim.id)
    db.commit()
    return _claim_out(db, claim)


# ---- Consent evidence review --------------------------------------------------

@router.get("/community/consent-reviews", response_model=list[ConsentOut])
def pending_consent_reviews(user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    rows = db.scalars(select(ConsentEvent).join(Resident, ConsentEvent.resident_id == Resident.id)
                      .where(Resident.community_id == user.community_id,
                             ConsentEvent.method == "paper_assisted",
                             ConsentEvent.review_status == "pending")
                      .order_by(ConsentEvent.id.desc()).limit(100)).all()
    return rows


@router.post("/community/consent-events/{event_id}/review", response_model=ConsentOut)
def review_consent_event(event_id: int, body: ConsentReviewIn, user: User = Depends(require_roles("staff")), db: Session = Depends(get_db)):
    event = db.get(ConsentEvent, event_id)
    if not event or event.method != "paper_assisted":
        raise HTTPException(404, "记录不存在")
    resident = db.get(Resident, event.resident_id)
    if not resident or resident.community_id != user.community_id:
        raise HTTPException(404, "记录不存在")
    if event.review_status != "pending":
        raise HTTPException(409, "该记录已复核")
    event.review_status = "approved" if body.approve else "rejected"
    event.review_note = body.note
    event.reviewed_by_id = user.id
    event.reviewed_at = datetime.now(timezone.utc)
    audit(db, user, "approve_consent_evidence" if body.approve else "reject_consent_evidence", "consent_event", event.id)
    db.commit()
    return event
