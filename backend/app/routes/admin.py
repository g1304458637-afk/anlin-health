"""Global admin: account lifecycle only. Admins still get NO personal health data."""
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from ..db import get_db
from ..models import Community, User
from ..deps import require_roles, audit
from ..ratelimit import SlidingWindowLimiter
from ..security import hash_password, new_invite_code, validate_password_strength
from ..schemas import AdminUserOut, CreateStaffIn

router = APIRouter(prefix="/api/admin", tags=["admin"])
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fa5]{4,40}$")
create_staff_limiter = SlidingWindowLimiter()


def _user_out(u: User) -> AdminUserOut:
    return AdminUserOut(id=u.id, username=u.username, display_name=u.display_name, role=u.role,
                        community_id=u.community_id, is_active=u.is_active,
                        mfa_enabled=bool(u.totp_secret), last_login_at=u.last_login_at,
                        password_updated_at=u.password_updated_at)


@router.get("/users", response_model=list[AdminUserOut])
def list_users(user: User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    rows = db.scalars(select(User).order_by(User.id).limit(500)).all()
    return [_user_out(u) for u in rows]


@router.post("/users", response_model=AdminUserOut, status_code=201)
def create_staff(body: CreateStaffIn, actor: User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    if not create_staff_limiter.hit(str(actor.id), 10, window_seconds=3600):
        raise HTTPException(429, "开通工作人员账号过于频繁，请稍后再试")
    if not USERNAME_RE.fullmatch(body.username):
        raise HTTPException(422, "用户名需为4-40位字母、数字或_.-")
    reason = validate_password_strength(body.initial_password)
    if reason:
        raise HTTPException(422, reason)
    if not db.get(Community, body.community_id):
        raise HTTPException(404, "社区不存在")
    if db.scalar(select(User.id).where(User.username == body.username)):
        raise HTTPException(409, "用户名已被使用")
    account = User(username=body.username, display_name=body.display_name, role="staff",
                   community_id=body.community_id, password_hash=hash_password(body.initial_password),
                   invite_code=new_invite_code())
    db.add(account)
    try:
        db.flush()
        audit(db, actor, "create_staff_account", "user", account.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "用户名已被使用") from None
    return _user_out(account)


@router.post("/users/{user_id}/deactivate", status_code=204)
def deactivate_user(user_id: int, actor: User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "账号不存在")
    if target.id == actor.id:
        raise HTTPException(409, "不能停用自己的账号")
    if target.role == "admin":
        raise HTTPException(409, "不能停用管理员账号，请通过运维流程处理")
    if target.is_active:
        target.is_active = False
        target.deactivated_by_id = actor.id
        target.token_version += 1  # kill live sessions immediately
        audit(db, actor, "deactivate_user", "user", target.id)
        db.commit()


@router.post("/users/{user_id}/activate", status_code=204)
def activate_user(user_id: int, actor: User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "账号不存在")
    if not target.is_active:
        target.is_active = True
        target.deactivated_by_id = None
        target.failed_login_count = 0
        target.locked_until = None
        target.token_version += 1
        audit(db, actor, "activate_user", "user", target.id)
        db.commit()
