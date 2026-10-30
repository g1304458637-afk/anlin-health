from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from ..db import get_db
from ..models import User, Resident, FamilyGrant
from ..deps import auth_user, is_owner, active_grant, audit
from ..security import new_invite_code
from ..schemas import FamilyGrantIn, FamilyGrantOut

router = APIRouter(prefix="/api", tags=["family"])


@router.post("/me/invite-code/rotate")
def rotate_invite_code(user: User = Depends(auth_user), db: Session = Depends(get_db)):
    """Replace the user's family invite code; the old code stops working immediately."""
    if user.role not in {"resident", "family"}:
        raise HTTPException(403, "仅居民/家属账号可轮换邀请码")
    user.invite_code = new_invite_code()
    audit(db, user, "rotate_invite_code", "user", user.id)
    db.commit()
    return {"invite_code": user.invite_code}


def grant_output(db: Session, g: FamilyGrant) -> FamilyGrantOut:
    member = db.get(User, g.grantee_id)
    return FamilyGrantOut(id=g.id, resident_id=g.resident_id, grantee_id=g.grantee_id,
        grantee_name=member.display_name if member else "已停用账号", can_profile=g.can_profile,
        can_vitals=g.can_vitals, can_write_vitals=g.can_write_vitals,
        expires_at=g.expires_at, revoked_at=g.revoked_at)


@router.post("/residents/{resident_id}/family-grants", response_model=FamilyGrantOut, status_code=201)
def grant_family(resident_id: int, body: FamilyGrantIn, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    resident = db.get(Resident, resident_id)
    if not resident or not is_owner(user, resident):
        raise HTTPException(404, "仅档案本人可以授权")
    recipient = db.scalar(select(User).where(User.invite_code == body.invite_code, User.is_active.is_(True)))
    if not recipient or recipient.role != "family":
        raise HTTPException(422, "家庭成员邀请码无效")
    if recipient.id == user.id:
        raise HTTPException(422, "不能授权给自己")
    now = datetime.now(timezone.utc)
    for old in db.scalars(select(FamilyGrant).where(FamilyGrant.resident_id == resident_id, FamilyGrant.grantee_id == recipient.id, FamilyGrant.revoked_at.is_(None))).all():
        old.revoked_at = now
    g = FamilyGrant(resident_id=resident_id, grantee_id=recipient.id, can_profile=body.can_profile,
                    can_vitals=body.can_vitals, can_write_vitals=body.can_write_vitals,
                    expires_at=now + timedelta(days=body.expires_in_days))
    db.add(g)
    db.flush()
    audit(db, user, "grant_family_access", "family_grant", g.id)
    db.commit()
    return grant_output(db, g)


@router.get("/residents/{resident_id}/family-grants", response_model=list[FamilyGrantOut])
def list_family_grants(resident_id: int, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    resident = db.get(Resident, resident_id)
    if not resident or not is_owner(user, resident):
        raise HTTPException(404, "无权查看")
    grants = db.scalars(select(FamilyGrant).where(FamilyGrant.resident_id == resident_id).order_by(FamilyGrant.id.desc()).limit(100)).all()
    return [grant_output(db, x) for x in grants]


@router.delete("/residents/{resident_id}/family-grants/{grant_id}", status_code=204)
def revoke_family(resident_id: int, grant_id: int, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    resident = db.get(Resident, resident_id)
    if not resident or not is_owner(user, resident):
        raise HTTPException(404, "无权查看")
    g = db.get(FamilyGrant, grant_id)
    if not g or g.resident_id != resident_id:
        raise HTTPException(404, "授权不存在")
    if not g.revoked_at:
        g.revoked_at = datetime.now(timezone.utc)
        audit(db, user, "revoke_family_access", "family_grant", g.id)
        db.commit()


@router.post("/residents/{resident_id}/family-grants/{grant_id}/decline", status_code=204)
def decline_family_grant(resident_id: int, grant_id: int, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    """Grantee-side refusal: the invited family member can reject a grant made to them."""
    g = db.get(FamilyGrant, grant_id)
    if not g or g.resident_id != resident_id or g.grantee_id != user.id:
        raise HTTPException(404, "授权不存在")
    if not g.revoked_at:
        g.revoked_at = datetime.now(timezone.utc)
        audit(db, user, "decline_family_grant", "family_grant", g.id)
        db.commit()


@router.get("/family/shared-residents")
def shared_residents(user: User = Depends(auth_user), db: Session = Depends(get_db)):
    if user.role != "family":
        raise HTTPException(403, "仅家庭成员可使用")
    grants = db.scalars(select(FamilyGrant).where(FamilyGrant.grantee_id == user.id,
        FamilyGrant.revoked_at.is_(None), FamilyGrant.expires_at > datetime.now(timezone.utc))
        .order_by(FamilyGrant.id.desc())).all()
    result, seen = [], set()
    for g in grants:
        if g.resident_id in seen:
            continue
        r = db.get(Resident, g.resident_id)
        if not r:
            continue
        profile = active_grant(db, user, r, "profile")
        vitals = active_grant(db, user, r, "vitals")
        if not (profile or vitals):
            continue
        result.append({"resident_id": r.id, "grant_id": g.id, "expires_at": g.expires_at,
                       "name": r.name if profile else "授权居民", "can_profile": profile, "can_vitals": vitals,
                       "can_write_vitals": active_grant(db, user, r, "vitals") and any(
                           g.can_write_vitals for g in grants if g.resident_id == r.id and not g.revoked_at
                           and g.expires_at.replace(tzinfo=None) > datetime.now(timezone.utc).replace(tzinfo=None))})
        seen.add(r.id)
    return result
