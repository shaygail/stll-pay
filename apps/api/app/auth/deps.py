"""Request authentication, CSRF protection and merchant access checks."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db, set_context
from app.core.errors import ApiError, forbidden, not_found
from app.core.rbac import STEP_UP_MINUTES, Perm, has_perm
from app.core.security import constant_time_equals, token_hash
from app.models import Merchant, MerchantMembership, User, UserSession

CSRF_HEADER = "x-csrf-token"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def now_utc() -> datetime:
    return datetime.now(UTC)


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@dataclass
class AuthContext:
    user: User
    session: UserSession

    @property
    def mfa_verified(self) -> bool:
        return self.session.mfa_verified_at is not None and self.user.mfa_enabled_at is not None

    def mfa_age(self) -> timedelta | None:
        if self.session.mfa_verified_at is None:
            return None
        return now_utc() - self.session.mfa_verified_at


def check_csrf(request: Request) -> None:
    """Double-submit token plus Origin check for cookie-authenticated unsafe requests."""
    if request.method in _SAFE_METHODS:
        return
    settings = get_settings()
    origin = request.headers.get("origin")
    if origin is not None and origin.rstrip("/") != settings.web_origin.rstrip("/"):
        raise ApiError(403, "csrf_failed", "This request came from an unexpected origin.")
    cookie = request.cookies.get(settings.csrf_cookie_name)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not constant_time_equals(cookie, header):
        raise ApiError(403, "csrf_failed", "Missing or invalid CSRF token. Reload and try again.")


async def _load_session(request: Request, db: AsyncSession) -> AuthContext:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise ApiError(401, "unauthenticated", "Please log in.")
    row = (
        await db.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(UserSession.token_hash == token_hash(token))
        )
    ).one_or_none()
    if row is None:
        raise ApiError(401, "unauthenticated", "Please log in.")
    sess, user = row
    now = now_utc()
    idle_limit = sess.last_seen_at + timedelta(minutes=settings.session_idle_minutes)
    if sess.revoked_at or sess.expires_at <= now or idle_limit <= now or user.deleted_at:
        raise ApiError(401, "session_expired", "Your session has ended. Please log in again.")
    check_csrf(request)
    if now - sess.last_seen_at > timedelta(seconds=30):
        sess.last_seen_at = now
    await set_context(db, user_id=user.id, is_platform_admin=user.is_platform_admin)
    return AuthContext(user=user, session=sess)


async def pending_auth(request: Request, db: AsyncSession = Depends(get_db)) -> AuthContext:
    """A logged-in session that may still need to complete MFA."""
    return await _load_session(request, db)


async def current_auth(request: Request, db: AsyncSession = Depends(get_db)) -> AuthContext:
    """A fully authenticated session: password plus MFA."""
    auth = await _load_session(request, db)
    if not auth.mfa_verified:
        raise ApiError(401, "mfa_required", "Finish signing in with your authenticator code.")
    return auth


def require_fresh_mfa(minutes: int) -> Callable[..., Awaitable[AuthContext]]:
    async def _dep(auth: AuthContext = Depends(current_auth)) -> AuthContext:
        age = auth.mfa_age()
        if age is None or age > timedelta(minutes=minutes):
            raise ApiError(401, "step_up_required",
                           "Confirm it's you with your authenticator code to continue.",
                           {"max_age_minutes": minutes})
        return auth

    return _dep


async def platform_admin(
    auth: AuthContext = Depends(require_fresh_mfa(get_settings().mfa_fresh_minutes)),
) -> AuthContext:
    if not auth.user.is_platform_admin:
        raise not_found()
    return auth


@dataclass
class MerchantAccess:
    auth: AuthContext
    merchant: Merchant
    role: str | None  # None when a platform admin acts without a membership


def merchant_access(
    perm: Perm, *, step_up: bool = False
) -> Callable[..., Awaitable[MerchantAccess]]:
    auth_dep = require_fresh_mfa(STEP_UP_MINUTES) if step_up else current_auth

    async def _dep(
        merchant_id: uuid.UUID,
        auth: AuthContext = Depends(auth_dep),
        db: AsyncSession = Depends(get_db),
    ) -> MerchantAccess:
        membership = (
            await db.execute(
                select(MerchantMembership).where(
                    MerchantMembership.merchant_id == merchant_id,
                    MerchantMembership.user_id == auth.user.id,
                )
            )
        ).scalar_one_or_none()
        if membership is None and not auth.user.is_platform_admin:
            raise not_found("Merchant not found")
        await set_context(db, merchant_id=merchant_id)
        merchant = (
            await db.execute(select(Merchant).where(Merchant.id == merchant_id))
        ).scalar_one_or_none()
        if merchant is None or merchant.deleted_at is not None:
            raise not_found("Merchant not found")
        if membership is None:
            # Platform admins get read-only access to merchant workspaces.
            if perm not in (Perm.MERCHANT_READ, Perm.TEAM_READ, Perm.AUDIT_READ,
                            Perm.PAYMENTS_READ):
                raise forbidden()
            return MerchantAccess(auth, merchant, None)
        if not has_perm(membership.role, perm):
            raise forbidden()
        return MerchantAccess(auth, merchant, membership.role)

    return _dep
