"""Authentication endpoints: sign-up, email verification, login, MFA, step-up, logout."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import (
    AuthContext,
    check_csrf,
    client_ip,
    current_auth,
    now_utc,
    pending_auth,
)
from app.core import audit
from app.core.config import get_settings
from app.core.db import get_db, get_sessionmaker, set_context
from app.core.email import send_email
from app.core.errors import ApiError
from app.core.ratelimit import limiter
from app.core.security import (
    decrypt_field,
    encrypt_field,
    hash_password,
    new_token,
    new_totp_secret,
    password_needs_rehash,
    password_problems,
    token_hash,
    totp_uri,
    verify_password,
    verify_totp,
)
from app.models import Merchant, MerchantMembership, User, UserSession, UserToken

router = APIRouter(prefix="/v1/auth", tags=["auth"])
me_router = APIRouter(prefix="/v1", tags=["auth"])

VERIFY_TOKEN_HOURS = 24


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SignupIn(Strict):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    display_name: str | None = Field(default=None, max_length=80)


class LoginIn(Strict):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class TokenIn(Strict):
    token: str = Field(min_length=10, max_length=200)


class CodeIn(Strict):
    code: str = Field(min_length=6, max_length=8)


def origin_guard(request: Request) -> None:
    """Pre-session endpoints have no CSRF cookie yet; reject cross-site browser posts."""
    origin = request.headers.get("origin")
    if origin is not None and origin.rstrip("/") != get_settings().web_origin.rstrip("/"):
        raise ApiError(403, "csrf_failed", "This request came from an unexpected origin.")


Db = Annotated[AsyncSession, Depends(get_db)]


def _set_auth_cookies(response: Response, session_token: str) -> None:
    settings = get_settings()
    max_age = settings.session_absolute_hours * 3600
    response.set_cookie(settings.session_cookie_name, session_token, max_age=max_age,
                        httponly=True, secure=settings.secure_cookies, samesite="lax", path="/")
    # Readable by the web app so it can echo it in the X-CSRF-Token header.
    response.set_cookie(settings.csrf_cookie_name, new_token(), max_age=max_age,
                        httponly=False, secure=settings.secure_cookies, samesite="lax", path="/")


def _clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    for name in (settings.session_cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(name, path="/", secure=settings.secure_cookies, samesite="lax")


async def _new_session(db: AsyncSession, user: User, request: Request,
                       mfa_verified: bool) -> str:
    settings = get_settings()
    token = new_token()
    now = now_utc()
    db.add(UserSession(
        user_id=user.id, token_hash=token_hash(token), last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_absolute_hours),
        mfa_verified_at=now if mfa_verified else None, ip=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300],
    ))
    await db.flush()
    return token


def _session_state(auth: AuthContext) -> dict[str, object]:
    return {
        "authenticated": True,
        "mfa_enrolled": auth.user.mfa_enabled_at is not None,
        "mfa_verified": auth.mfa_verified,
        "email": auth.user.email,
    }


# ---------------------------------------------------------------- sign-up


@router.post("/signup", status_code=202, dependencies=[Depends(origin_guard)])
async def signup(body: SignupIn, request: Request, db: Db) -> dict[str, str]:
    ip = client_ip(request) or "unknown"
    limiter.check(f"signup:ip:{ip}", 10, 3600)
    email = body.email.lower()
    problems = password_problems(body.password, email)
    if problems:
        raise ApiError(422, "weak_password", problems[0], {"problems": problems})

    existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    message = {"message": "Check your email to finish creating your account."}
    if existing is not None:
        # Same response either way, so sign-up can't be used to discover accounts.
        send_email(email, "Someone tried to sign up with your email",
                   "If this was you, log in instead or reset your password.")
        return message

    user = User(email=email, password_hash=hash_password(body.password),
                display_name=body.display_name)
    db.add(user)
    await db.flush()
    token = new_token()
    db.add(UserToken(user_id=user.id, purpose="verify_email", token_hash=token_hash(token),
                     expires_at=now_utc() + timedelta(hours=VERIFY_TOKEN_HOURS)))
    link = f"{get_settings().web_origin}/verify-email?token={token}"
    send_email(email, "Verify your STLL PAY email", f"Open this link to verify: {link}")
    await audit.record(db, "user.signup", actor_user_id=user.id, target_type="user",
                       target_id=str(user.id), ip=ip)
    return message


@router.post("/verify-email", dependencies=[Depends(origin_guard)])
async def verify_email(body: TokenIn, request: Request, db: Db) -> dict[str, str]:
    limiter.check(f"verify:ip:{client_ip(request)}", 20, 3600)
    row = (await db.execute(
        select(UserToken).where(UserToken.token_hash == token_hash(body.token),
                                UserToken.purpose == "verify_email")
    )).scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at <= now_utc():
        raise ApiError(400, "invalid_token", "This link is invalid or has expired.")
    row.used_at = now_utc()
    await db.execute(update(User).where(User.id == row.user_id, User.email_verified_at.is_(None))
                     .values(email_verified_at=now_utc()))
    await audit.record(db, "user.email_verified", actor_user_id=row.user_id,
                       target_type="user", target_id=str(row.user_id), ip=client_ip(request))
    return {"message": "Email verified. You can log in now."}


# ---------------------------------------------------------------- login


async def _record_failed_login(user_id: object, ip: str) -> None:
    """Count a failed password in its own transaction, so the failing request's
    rollback doesn't undo it. Locks the account after too many failures."""
    settings = get_settings()
    async with get_sessionmaker()() as s, s.begin():
        user = (await s.execute(select(User).where(User.id == user_id).with_for_update())
                ).scalar_one()
        user.failed_login_count += 1
        if user.failed_login_count >= settings.login_max_failures:
            user.locked_until = now_utc() + timedelta(minutes=settings.login_lockout_minutes)
            user.failed_login_count = 0
            await audit.record(s, "user.locked", actor_user_id=user.id, target_type="user",
                               target_id=str(user.id), ip=ip)
        await audit.record(s, "user.login_failed", actor_user_id=user.id, ip=ip)


@router.post("/login", dependencies=[Depends(origin_guard)])
async def login(body: LoginIn, request: Request, response: Response, db: Db) -> dict[str, object]:
    ip = client_ip(request) or "unknown"
    email = body.email.lower()
    limiter.check(f"login:ip:{ip}", 30, 900)
    limiter.check(f"login:email:{email}", 10, 900)

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    now = now_utc()
    invalid = ApiError(401, "invalid_credentials", "That email and password don't match.")

    if user is not None and user.locked_until and user.locked_until > now:
        raise ApiError(423, "account_locked",
                       "Too many failed attempts. Try again in a few minutes.")
    if not verify_password(user.password_hash if user else None, body.password) or user is None:
        if user is not None:
            await _record_failed_login(user.id, ip)
        raise invalid
    if user.deleted_at is not None:
        raise invalid
    if user.email_verified_at is None:
        raise ApiError(403, "email_unverified", "Verify your email before logging in.")

    user.failed_login_count = 0
    user.locked_until = None
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    token = await _new_session(db, user, request, mfa_verified=False)
    _set_auth_cookies(response, token)
    await audit.record(db, "user.login_password_ok", actor_user_id=user.id, ip=ip)
    return {"mfa_required": True, "mfa_enrolled": user.mfa_enabled_at is not None}


@router.get("/session")
async def session_state(auth: AuthContext = Depends(pending_auth)) -> dict[str, object]:
    return _session_state(auth)


@router.post("/logout")
async def logout(request: Request, response: Response, db: Db) -> dict[str, bool]:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        check_csrf(request)
        await db.execute(update(UserSession)
                         .where(UserSession.token_hash == token_hash(token),
                                UserSession.revoked_at.is_(None))
                         .values(revoked_at=now_utc()))
    _clear_auth_cookies(response)
    return {"ok": True}


# ---------------------------------------------------------------- MFA


@router.post("/mfa/setup")
async def mfa_setup(db: Db, auth: AuthContext = Depends(pending_auth)) -> dict[str, str]:
    if auth.user.mfa_enabled_at is not None:
        raise ApiError(409, "mfa_already_enabled", "Two-step verification is already set up.")
    secret = new_totp_secret()
    auth.user.mfa_secret_enc = encrypt_field(secret)
    await db.flush()
    return {"secret": secret, "otpauth_uri": totp_uri(secret, auth.user.email)}


async def _rotate_session(db: AsyncSession, auth: AuthContext, request: Request,
                          response: Response) -> None:
    """Replace the session after MFA so a pre-MFA token can't be reused."""
    auth.session.revoked_at = now_utc()
    token = await _new_session(db, auth.user, request, mfa_verified=True)
    _set_auth_cookies(response, token)


def _check_code(auth: AuthContext, code: str) -> int:
    limiter.check(f"mfa:user:{auth.user.id}", 5, 300)
    if not auth.user.mfa_secret_enc:
        raise ApiError(409, "mfa_not_set_up", "Set up two-step verification first.")
    step = verify_totp(decrypt_field(auth.user.mfa_secret_enc), code, auth.user.mfa_last_step)
    if step is None:
        raise ApiError(401, "invalid_code", "That code didn't work. Check your authenticator app.")
    return step


@router.post("/mfa/enable")
async def mfa_enable(body: CodeIn, request: Request, response: Response, db: Db,
                     auth: AuthContext = Depends(pending_auth)) -> dict[str, object]:
    if auth.user.mfa_enabled_at is not None:
        raise ApiError(409, "mfa_already_enabled", "Two-step verification is already set up.")
    step = _check_code(auth, body.code)
    auth.user.mfa_last_step = step
    auth.user.mfa_enabled_at = now_utc()
    await _rotate_session(db, auth, request, response)
    await audit.record(db, "user.mfa_enabled", actor_user_id=auth.user.id, ip=client_ip(request))
    return {"mfa_verified": True}


@router.post("/mfa/verify")
async def mfa_verify(body: CodeIn, request: Request, response: Response, db: Db,
                     auth: AuthContext = Depends(pending_auth)) -> dict[str, object]:
    if auth.user.mfa_enabled_at is None:
        raise ApiError(409, "mfa_not_set_up", "Set up two-step verification first.")
    auth.user.mfa_last_step = _check_code(auth, body.code)
    await _rotate_session(db, auth, request, response)
    await audit.record(db, "user.login_mfa_ok", actor_user_id=auth.user.id, ip=client_ip(request))
    return {"mfa_verified": True}


@router.post("/step-up")
async def step_up(body: CodeIn, request: Request, db: Db,
                  auth: AuthContext = Depends(current_auth)) -> dict[str, object]:
    auth.user.mfa_last_step = _check_code(auth, body.code)
    auth.session.mfa_verified_at = now_utc()
    await audit.record(db, "user.step_up", actor_user_id=auth.user.id, ip=client_ip(request))
    return {"mfa_verified": True}


# ---------------------------------------------------------------- me


@me_router.get("/me")
async def me(db: Db, auth: AuthContext = Depends(current_auth)) -> dict[str, object]:
    await set_context(db, user_id=auth.user.id)
    rows = (await db.execute(
        select(MerchantMembership, Merchant)
        .join(Merchant, Merchant.id == MerchantMembership.merchant_id)
        .where(MerchantMembership.user_id == auth.user.id, Merchant.deleted_at.is_(None))
        .order_by(Merchant.trading_name)
    )).all()
    return {
        "id": str(auth.user.id),
        "email": auth.user.email,
        "display_name": auth.user.display_name,
        "is_platform_admin": auth.user.is_platform_admin,
        "mfa_verified_at": auth.session.mfa_verified_at,
        "memberships": [
            {"merchant_id": str(m.id), "trading_name": m.trading_name, "slug": m.slug,
             "status": m.status, "role": mm.role}
            for mm, m in rows
        ],
    }
