import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from ..db import get_db
from ..config import settings
from ..models import User, Resident
from ..security import verify_password, access_token, new_invite_code
from ..deps import auth_user, audit
from ..schemas import LoginIn, WxLoginIn, UserOut

router = APIRouter(prefix="/api", tags=["auth"])


def user_out(u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, display_name=u.display_name, role=u.role, community_id=u.community_id, invite_code=u.invite_code)


@router.post("/auth/dev-login")
def dev_login(body: LoginIn, db: Session = Depends(get_db)):
    # Intentional development-only endpoint: no static demo secrets in production.
    if settings.app_env == "production" or not settings.demo_auth:
        raise HTTPException(404, "未启用")
    user = db.scalar(select(User).where(User.username == body.username))
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    audit(db, user, "dev_login", "user", user.id)
    db.commit()
    return {"access_token": access_token(user.id), "token_type": "bearer", "user": user_out(user)}


async def exchange_wechat_code(code: str) -> str:
    if not settings.wechat_appid or not settings.wechat_appsecret:
        raise HTTPException(503, "尚未配置微信小程序凭据")
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            result = await client.get("https://api.weixin.qq.com/sns/jscode2session", params={
                "appid": settings.wechat_appid, "secret": settings.wechat_appsecret,
                "js_code": code, "grant_type": "authorization_code"
            })
            result.raise_for_status()
            payload = result.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "微信登录接口暂时不可用")
    if not payload.get("openid"):
        raise HTTPException(401, "微信登录失败，请重新尝试")
    # The session_key must never be returned or logged.
    return payload["openid"]


@router.post("/auth/wechat")
async def wechat_login(body: WxLoginIn, db: Session = Depends(get_db)):
    openid = await exchange_wechat_code(body.code)
    user = db.scalar(select(User).where(User.wechat_openid == openid))
    if not user:
        # Onboarding is deliberately limited to an unprivileged resident account.
        code = new_invite_code()
        user = User(username=f"wx_{code}", display_name="微信居民", role="resident", invite_code=code, wechat_openid=openid)
        db.add(user)
        db.flush()
    if not user.is_active:
        raise HTTPException(403, "账号不可用")
    audit(db, user, "wechat_login", "user", user.id)
    db.commit()
    return {"access_token": access_token(user.id), "token_type": "bearer", "user": user_out(user)}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(auth_user)):
    return user_out(user)


@router.post("/me/family-mode", response_model=UserOut)
def family_mode(user: User = Depends(auth_user), db: Session = Depends(get_db)):
    if user.role != "resident":
        raise HTTPException(403, "仅未建档居民可切换家庭身份")
    if db.scalar(select(Resident.id).where(Resident.owner_user_id == user.id)):
        raise HTTPException(409, "已有居民档案，不允许直接转换角色")
    user.role = "family"
    audit(db, user, "switch_to_family", "user", user.id)
    db.commit()
    return user_out(user)
