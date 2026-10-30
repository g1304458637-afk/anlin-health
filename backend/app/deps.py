from datetime import datetime, timezone
import json

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import get_db
from .models import User, Resident, FamilyGrant, ConsentEvent, AuditLog
from .security import decode_token
import jwt

bearer = HTTPBearer(auto_error=False)


def auth_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> User:
    if not credentials:
        raise HTTPException(401, "请先登录")
    try:
        uid, token_version = decode_token(credentials.credentials)
    except (jwt.PyJWTError, ValueError, TypeError):
        raise HTTPException(401, "登录已失效")
    user = db.get(User, uid)
    if not user or not user.is_active:
        raise HTTPException(401, "账号不可用")
    # Password change or global sign-out bumps token_version; older tokens die here.
    if token_version != user.token_version:
        raise HTTPException(401, "登录已失效，请重新登录")
    return user


def require_roles(*roles):
    def dependency(user: User = Depends(auth_user)):
        if user.role not in roles:
            raise HTTPException(403, "无此操作权限")
        return user
    return dependency


def consent_active(db: Session, resident_id: int) -> bool:
    ev = db.scalar(select(ConsentEvent).where(ConsentEvent.resident_id == resident_id, ConsentEvent.purpose == "community_care").order_by(ConsentEvent.id.desc()).limit(1))
    if not ev or not ev.granted:
        return False
    # The latest consent decision is authoritative. An unreviewed paper event
    # cannot revive access after a resident has withdrawn consent.
    return ev.review_status == "approved"


def is_owner(user: User, resident: Resident) -> bool:
    return resident.owner_user_id is not None and resident.owner_user_id == user.id


def is_staff_for(user: User, resident: Resident) -> bool:
    return user.role == "staff" and user.community_id == resident.community_id


def family_can_write_vitals(db: Session, user: User, resident: Resident) -> bool:
    # An active grant that explicitly allows entering readings on the resident's behalf.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    grants = db.scalars(select(FamilyGrant).where(FamilyGrant.grantee_id == user.id,
        FamilyGrant.resident_id == resident.id, FamilyGrant.revoked_at.is_(None))).all()
    return any(g.expires_at and g.expires_at.replace(tzinfo=None) > now
               and g.can_vitals and g.can_write_vitals for g in grants)


def active_grant(db: Session, user: User, resident: Resident, scope: str) -> bool:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    grants = db.scalars(select(FamilyGrant).where(FamilyGrant.grantee_id == user.id, FamilyGrant.resident_id == resident.id, FamilyGrant.revoked_at.is_(None))).all()
    return any(g.expires_at and g.expires_at.replace(tzinfo=None) > now and bool(getattr(g, f"can_{scope}")) for g in grants)


def check_access(db: Session, user: User, resident: Resident | None, scope: str = "profile", write: bool = False) -> Resident:
    if resident is None:
        raise HTTPException(404, "档案不存在")
    if is_owner(user, resident):
        return resident
    if is_staff_for(user, resident) and consent_active(db, resident.id):
        return resident
    if not write and scope in {"profile", "vitals"} and user.role == "family" and active_grant(db, user, resident, scope):
        return resident
    if write and scope == "vitals" and user.role == "family" and family_can_write_vitals(db, user, resident):
        return resident
    # Do not disclose whether an inaccessible record exists.
    raise HTTPException(404, "档案不存在或无权访问")


def audit(db: Session, user: User, action: str, entity: str, entity_id: int, detail: dict | None = None):
    """Append an audit entry; `detail` snapshots affected values (never bulk data)
    so destructive actions stay explainable afterwards."""
    db.add(AuditLog(actor_id=user.id, action=action, entity=entity, entity_id=entity_id,
                    detail_json=json.dumps(detail, ensure_ascii=False) if detail else None))
