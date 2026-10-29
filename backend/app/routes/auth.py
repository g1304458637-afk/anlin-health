import hashlib
import re
import random
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..db import get_db
from ..config import settings
from ..models import User, Resident, ClaimRequest, SingleUseToken
from ..ratelimit import login_limiter, wechat_limiter, register_limiter, client_ip
from ..security import verify_password, validate_password_strength, hash_password, access_token, new_invite_code
from ..deps import auth_user, audit
from ..totp import match_totp_counter
from ..schemas import (LoginIn, WxLoginIn, PasswordChangeIn, TotpLoginIn, RegisterFamilyIn, UserOut,
                       TotpSetupIn, TotpConfirmIn, TotpDisableIn)

router = APIRouter(prefix="/api", tags=["auth"])

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fa5]{4,40}$")  # 中文用户名对老年居民更友好

_dummy_hash: str | None = None


def _timing_equalizer_hash() -> str:
    """A throwaway PBKDF2 hash so unknown usernames cost the same as wrong
    passwords and cannot be enumerated by response timing."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("timing-equalizer-占位-Abcdef9527")
    return _dummy_hash


def _consume_wx_code(db: Session, code: str) -> bool:
    """Mark a WeChat code redeemed; return False when it was already used.

    Stored as a hash in `single_use_tokens`, so replay protection holds across
    processes (the previous per-process dict silently allowed replays when more
    than one worker was running). Codes are single-use on WeChat's side as well,
    so rows only need to outlive a short replay window.
    """
    token_hash = hashlib.sha256(code.encode()).hexdigest()
    db.add(SingleUseToken(kind="wx_code", token_hash=token_hash))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    if random.random() < 0.05:
        db.execute(delete(SingleUseToken).where(
            SingleUseToken.kind == "wx_code",
            SingleUseToken.created_at < datetime.now(timezone.utc) - timedelta(days=1)))
        db.commit()
    return True


def user_out(u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, display_name=u.display_name, role=u.role, community_id=u.community_id, invite_code=u.invite_code)


def _issue_login(user: User, db: Session, action: str) -> dict:
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = datetime.now(timezone.utc)
    audit(db, user, action, "user", user.id)
    db.commit()
    return {"access_token": access_token(user.id, user.token_version), "token_type": "bearer", "user": user_out(user)}


def _check_login_lock(user: User | None, now: datetime) -> None:
    if user and user.locked_until and user.locked_until.replace(tzinfo=None) > now.replace(tzinfo=None):
        raise HTTPException(423, "账号已临时锁定，请稍后再试或联系管理员")


def _record_login_failure(user: User | None, db: Session, now: datetime, action: str) -> None:
    if user:
        user.failed_login_count += 1
        audit(db, user, action, "user", user.id)
        if user.failed_login_count >= settings.login_max_attempts:
            user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            audit(db, user, "account_locked", "user", user.id)
        db.commit()


def _verify_mfa_password(user: User, password: str, db: Session) -> None:
    now = datetime.now(timezone.utc)
    _check_login_lock(user, now)
    if not verify_password(password, user.password_hash):
        _record_login_failure(user, db, now, "mfa_password_failed")
        raise HTTPException(401, "当前密码不正确")


def _verify_current_factor(user: User, code: str | None, db: Session) -> None:
    """Verify the active factor and record its counter so the same shown code
    cannot be reused for a later confirmation or sign-in."""
    counter = match_totp_counter(user.totp_secret, code)
    if counter is None or counter <= user.totp_last_used_counter:
        _record_login_failure(user, db, datetime.now(timezone.utc), "mfa_totp_failed")
        raise HTTPException(401, "当前两步验证码不正确")
    user.totp_last_used_counter = counter


def _clear_auth_failures(user: User) -> None:
    user.failed_login_count = 0
    user.locked_until = None


@router.post("/auth/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    """Production-capable username/password login (staff provisioned out-of-band).

    Defenses: per-account+IP rate limit, per-account lockout after repeated
    failures, optional TOTP second factor, deactivation check, audit trail.
    """
    key = f"{client_ip(request)}:{body.username}"
    if not login_limiter.hit(key, settings.login_rate_per_minute):
        raise HTTPException(429, "尝试过于频繁，请稍后再试")
    user = db.scalar(select(User).where(User.username == body.username))
    now = datetime.now(timezone.utc)
    _check_login_lock(user, now)
    if not user:
        verify_password(body.password, _timing_equalizer_hash())
    if not user or not verify_password(body.password, user.password_hash):
        _record_login_failure(user, db, now, "password_login_failed")
        raise HTTPException(401, "用户名或密码错误")
    if not user.is_active:
        raise HTTPException(403, "账号已停用")
    if user.totp_secret:
        raise HTTPException(401, "此账号已开启两步验证", headers={"X-MFA-Required": "totp"})
    return _issue_login(user, db, "password_login")


@router.post("/auth/login/totp")
def login_totp(body: TotpLoginIn, request: Request, db: Session = Depends(get_db)):
    key = f"{client_ip(request)}:{body.username}"
    if not login_limiter.hit(key, settings.login_rate_per_minute):
        raise HTTPException(429, "尝试过于频繁，请稍后再试")
    user = db.scalar(select(User).where(User.username == body.username))
    now = datetime.now(timezone.utc)
    _check_login_lock(user, now)
    if not user or not user.totp_secret:
        verify_password(body.password, _timing_equalizer_hash())
        _record_login_failure(user, db, now, "password_login_failed")
        raise HTTPException(401, "用户名或密码错误")
    if not verify_password(body.password, user.password_hash):
        _record_login_failure(user, db, now, "password_login_failed")
        raise HTTPException(401, "用户名或密码错误")
    if not user.is_active:
        raise HTTPException(403, "账号已停用")
    counter = match_totp_counter(user.totp_secret, body.totp_code)
    if counter is None or counter <= user.totp_last_used_counter:
        _record_login_failure(user, db, now, "totp_failed")
        raise HTTPException(401, "两步验证码不正确")
    user.totp_last_used_counter = counter
    return _issue_login(user, db, "password_login_totp")


@router.post("/auth/dev-login")
def dev_login(body: LoginIn, db: Session = Depends(get_db)):
    # Intentional development-only endpoint: no static demo secrets in production.
    if settings.app_env == "production" or not settings.demo_auth:
        raise HTTPException(404, "未启用")
    user = db.scalar(select(User).where(User.username == body.username))
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    return _issue_login(user, db, "dev_login")


@router.get("/auth/demo-status")
def demo_status():
    # The web console calls this to decide whether to show demo-credential hints.
    return {"enabled": bool(settings.demo_auth and settings.app_env != "production")}


@router.post("/auth/change-password")
def change_password(body: PasswordChangeIn, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(401, "当前密码不正确")
    reason = validate_password_strength(body.new_password)
    if reason:
        raise HTTPException(422, reason)
    if verify_password(body.new_password, user.password_hash):
        raise HTTPException(422, "新密码不能与当前密码相同")
    user.password_hash = hash_password(body.new_password)
    user.password_updated_at = datetime.now(timezone.utc)
    user.token_version += 1  # invalidate every existing session everywhere
    audit(db, user, "change_password", "user", user.id)
    db.commit()
    return {"access_token": access_token(user.id, user.token_version), "token_type": "bearer"}


@router.post("/auth/logout-all")
def logout_all(user: User = Depends(auth_user), db: Session = Depends(get_db)):
    user.token_version += 1
    audit(db, user, "logout_all", "user", user.id)
    db.commit()
    return {"ok": True}


@router.post("/auth/mfa/totp")
def setup_totp(body: TotpSetupIn, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    """Stage a new factor; the active factor remains usable until confirmation."""
    from ..totp import new_totp_secret, provisioning_uri
    _verify_mfa_password(user, body.current_password, db)
    if user.totp_secret:
        _verify_current_factor(user, body.totp_code, db)
    secret = new_totp_secret()
    user.totp_pending_secret = secret
    user.totp_pending_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    user.totp_pending_failures = 0
    _clear_auth_failures(user)
    audit(db, user, "mfa_totp_staged", "user", user.id)
    db.commit()
    return {"secret": secret, "uri": provisioning_uri(secret, user.username)}


@router.post("/auth/mfa/totp/confirm")
def confirm_totp(body: TotpConfirmIn, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    _verify_mfa_password(user, body.current_password, db)
    now = datetime.now(timezone.utc)
    if not user.totp_pending_secret or not user.totp_pending_expires_at or user.totp_pending_expires_at.replace(tzinfo=None) <= now.replace(tzinfo=None):
        raise HTTPException(409, "待确认的两步验证已失效，请重新设置")
    counter = match_totp_counter(user.totp_pending_secret, body.totp_code)
    if counter is None or counter <= user.totp_last_used_counter:
        user.totp_pending_failures += 1
        if user.totp_pending_failures >= 5:
            user.totp_pending_secret = None
            user.totp_pending_expires_at = None
            user.totp_pending_failures = 0
        _record_login_failure(user, db, now, "mfa_totp_confirm_failed")
        raise HTTPException(401, "两步验证码不正确")
    user.totp_last_used_counter = counter
    user.totp_secret = user.totp_pending_secret
    user.totp_pending_secret = None
    user.totp_pending_expires_at = None
    user.totp_pending_failures = 0
    _clear_auth_failures(user)
    user.token_version += 1
    audit(db, user, "mfa_totp_enabled", "user", user.id)
    db.commit()
    return {"access_token": access_token(user.id, user.token_version), "token_type": "bearer"}


@router.delete("/auth/mfa/totp")
def disable_totp(body: TotpDisableIn, user: User = Depends(auth_user), db: Session = Depends(get_db)):
    if not user.totp_secret:
        raise HTTPException(404, "未开启两步验证")
    _verify_mfa_password(user, body.current_password, db)
    _verify_current_factor(user, body.totp_code, db)
    user.totp_secret = None
    user.totp_pending_secret = None
    user.totp_pending_expires_at = None
    user.totp_pending_failures = 0
    _clear_auth_failures(user)
    user.token_version += 1
    audit(db, user, "mfa_totp_disabled", "user", user.id)
    db.commit()
    return {"ok": True, "access_token": access_token(user.id, user.token_version), "token_type": "bearer"}


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
    if payload.get("errcode") in (40029, 40163, -1) or (payload.get("errcode") and not payload.get("openid")):
        # Invalid / replayed code or WeChat-side failure: the client must re-login.
        raise HTTPException(401, "微信登录凭据无效，请重新登录")
    if not payload.get("openid"):
        raise HTTPException(503, "微信登录接口暂时不可用")
    # The session_key must never be returned or logged.
    return payload["openid"]


@router.post("/auth/wechat")
async def wechat_login(body: WxLoginIn, request: Request, db: Session = Depends(get_db)):
    if not wechat_limiter.hit(client_ip(request), settings.wechat_login_rate_per_minute):
        raise HTTPException(429, "尝试过于频繁，请稍后再试")
    if not _consume_wx_code(db, body.code):
        raise HTTPException(401, "微信登录凭据无效，请重新登录")
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
    return _issue_login(user, db, "wechat_login")


@router.post("/auth/register-family", status_code=201)
def register_family(body: RegisterFamilyIn, request: Request, db: Session = Depends(get_db)):
    """Open family self-registration: a family account holds no data access until
    a resident redeems their secret invite code to grant it."""
    if not USERNAME_RE.fullmatch(body.username):
        raise HTTPException(422, "用户名需为4-40位字母、数字或_.-")
    if not register_limiter.hit(f"register:{client_ip(request)}", 10, window_seconds=3600):
        raise HTTPException(429, "注册过于频繁，请稍后再试")
    if db.scalar(select(User.id).where(User.username == body.username)):
        raise HTTPException(409, "用户名已被使用")
    reason = validate_password_strength(body.password)
    if reason:
        raise HTTPException(422, reason)
    user = User(username=body.username, display_name=body.display_name, role="family",
                invite_code=new_invite_code(), password_hash=hash_password(body.password))
    db.add(user)
    db.flush()
    audit(db, user, "register_family", "user", user.id)
    db.commit()
    return {"access_token": access_token(user.id, user.token_version), "token_type": "bearer", "user": user_out(user)}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(auth_user)):
    return user_out(user)


@router.post("/me/family-mode", response_model=UserOut)
def family_mode(user: User = Depends(auth_user), db: Session = Depends(get_db)):
    version = user.token_version
    # Serialize role changes with claim creation/review on databases with row locks.
    user = db.scalar(select(User).where(User.id == user.id).with_for_update()
                     .execution_options(populate_existing=True))
    if not user.is_active or user.token_version != version:
        raise HTTPException(401, "登录已失效，请重新登录")
    if user.role != "resident":
        raise HTTPException(403, "仅未建档居民可切换家庭身份")
    if db.scalar(select(Resident.id).where(Resident.owner_user_id == user.id)):
        raise HTTPException(409, "已有居民档案，不允许直接转换角色")
    if db.scalar(select(ClaimRequest.id).where(ClaimRequest.requester_id == user.id,
                                               ClaimRequest.status == "pending")):
        raise HTTPException(409, "有待审核的档案认领申请，请先联系工作人员处理后再切换身份")
    user.role = "family"
    audit(db, user, "switch_to_family", "user", user.id)
    db.commit()
    return user_out(user)
