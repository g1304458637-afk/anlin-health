"""Global admin: account lifecycle only. Admins still get NO personal health data."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from ..db import get_db
from ..models import User
from ..deps import require_roles, audit
from ..schemas import AdminUserOut

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users", response_model=list[AdminUserOut])
def list_users(user: User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    rows = db.scalars(select(User).order_by(User.id).limit(500)).all()
    return [AdminUserOut(id=u.id, username=u.username, display_name=u.display_name, role=u.role,
                         community_id=u.community_id, is_active=u.is_active,
                         mfa_enabled=bool(u.totp_secret), last_login_at=u.last_login_at,
                         password_updated_at=u.password_updated_at) for u in rows]


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
