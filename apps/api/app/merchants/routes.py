"""Merchant workspace and team management. Every route is scoped to one merchant."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import (
    AuthContext,
    MerchantAccess,
    client_ip,
    current_auth,
    merchant_access,
)
from app.core import audit, clock
from app.core.config import get_settings
from app.core.db import get_db, set_context
from app.core.email import send_email
from app.core.errors import ApiError, not_found
from app.core.ids import uuid7
from app.core.platform import multi_merchant_enabled
from app.core.rbac import Perm, Role
from app.core.security import new_token, token_hash
from app.models import AuditLog, Merchant, MerchantMembership, User, UserToken

router = APIRouter(prefix="/v1/merchants", tags=["merchants"])

# Not a valid Argon2 hash, so nobody can log in until the invitation is accepted.
INVITED_PASSWORD_HASH = "!invited"  # noqa: S105 - a marker, not a password
INVITE_DAYS = 7
Db = Annotated[AsyncSession, Depends(get_db)]

SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{1,46}[a-z0-9])$"
NZBN_PATTERN = r"^94[0-9]{11}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MerchantCreate(Strict):
    slug: str = Field(pattern=SLUG_PATTERN)
    legal_name: str = Field(min_length=1, max_length=200)
    trading_name: str = Field(min_length=1, max_length=120)
    nzbn: str | None = Field(default=None, pattern=NZBN_PATTERN)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=30)


class MerchantUpdate(Strict):
    # Status, live payments and slug are deliberately absent: extra fields are rejected.
    legal_name: str | None = Field(default=None, min_length=1, max_length=200)
    trading_name: str | None = Field(default=None, min_length=1, max_length=120)
    nzbn: str | None = Field(default=None, pattern=NZBN_PATTERN)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=30)


class MemberAdd(Strict):
    email: EmailStr
    role: Role


class MemberUpdate(Strict):
    role: Role


def merchant_out(m: Merchant, role: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(m.id), "slug": m.slug, "legal_name": m.legal_name,
        "trading_name": m.trading_name, "nzbn": m.nzbn, "contact_email": m.contact_email,
        "contact_phone": m.contact_phone, "status": m.status, "live_enabled": m.live_enabled,
        "created_at": m.created_at, "updated_at": m.updated_at,
    }
    if role is not None:
        out["role"] = role
    return out


@router.post("", status_code=201)
async def create_merchant(body: MerchantCreate, request: Request, db: Db,
                          auth: AuthContext = Depends(current_auth)) -> dict[str, Any]:
    if not auth.user.is_platform_admin and not await multi_merchant_enabled(db):
        raise ApiError(403, "merchant_creation_closed",
                       "New businesses can't be added yet. Contact STLL PAY.")
    merchant_id = uuid7()
    await set_context(db, merchant_id=merchant_id)
    merchant = Merchant(id=merchant_id, **body.model_dump())
    db.add(merchant)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise ApiError(409, "slug_taken", "That link name is already taken.") from exc
    db.add(MerchantMembership(merchant_id=merchant_id, user_id=auth.user.id, role=Role.OWNER))
    await db.flush()
    await db.refresh(merchant)
    await audit.record(db, "merchant.created", actor_user_id=auth.user.id,
                       merchant_id=merchant_id, target_type="merchant",
                       target_id=str(merchant_id), ip=client_ip(request),
                       metadata={"slug": body.slug})
    return merchant_out(merchant, Role.OWNER)


@router.get("/{merchant_id}")
async def get_merchant(access: MerchantAccess = Depends(merchant_access(Perm.MERCHANT_READ))
                       ) -> dict[str, Any]:
    return merchant_out(access.merchant, access.role)


@router.patch("/{merchant_id}")
async def update_merchant(body: MerchantUpdate, request: Request, db: Db,
                          access: MerchantAccess = Depends(merchant_access(Perm.MERCHANT_UPDATE))
                          ) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(access.merchant, key, value)
    await db.flush()
    await db.refresh(access.merchant)
    await audit.record(db, "merchant.updated", actor_user_id=access.auth.user.id,
                       merchant_id=access.merchant.id, target_type="merchant",
                       target_id=str(access.merchant.id), ip=client_ip(request),
                       metadata={"fields": sorted(changes)})
    return merchant_out(access.merchant, access.role)


@router.post("/{merchant_id}/submit")
async def submit_for_review(request: Request, db: Db,
                            access: MerchantAccess = Depends(merchant_access(Perm.MERCHANT_SUBMIT))
                            ) -> dict[str, Any]:
    if access.merchant.status != "draft":
        raise ApiError(409, "invalid_status", "Only a draft business can be submitted.")
    access.merchant.status = "pending_review"
    await db.flush()
    await audit.record(db, "merchant.submitted", actor_user_id=access.auth.user.id,
                       merchant_id=access.merchant.id, ip=client_ip(request))
    return merchant_out(access.merchant, access.role)


# ---------------------------------------------------------------- team


def member_out(mm: MerchantMembership, user: User) -> dict[str, Any]:
    return {"id": str(mm.id), "user_id": str(user.id), "email": user.email,
            "display_name": user.display_name, "role": mm.role, "created_at": mm.created_at,
            "invite_pending": user.email_verified_at is None}


@router.get("/{merchant_id}/members")
async def list_members(db: Db, access: MerchantAccess = Depends(merchant_access(Perm.TEAM_READ))
                       ) -> dict[str, Any]:
    rows = (await db.execute(
        select(MerchantMembership, User).join(User, User.id == MerchantMembership.user_id)
        .where(MerchantMembership.merchant_id == access.merchant.id)
        .order_by(MerchantMembership.created_at)
    )).all()
    return {"items": [member_out(mm, u) for mm, u in rows]}


@router.post("/{merchant_id}/members", status_code=201)
async def add_member(body: MemberAdd, request: Request, db: Db,
                     access: MerchantAccess = Depends(
                         merchant_access(Perm.TEAM_MANAGE, step_up=True))) -> dict[str, Any]:
    email = body.email.lower()
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    invite = False
    if user is None:
        # New person: create an account they finish through an emailed invitation.
        user = User(email=email, password_hash=INVITED_PASSWORD_HASH)
        db.add(user)
        await db.flush()
        invite = True
    elif user.deleted_at is not None:
        raise ApiError(409, "user_unavailable", "That account can't be added.")
    elif user.email_verified_at is None:
        if user.password_hash != INVITED_PASSWORD_HASH:
            raise ApiError(409, "user_unverified",
                           "That person hasn't verified their email yet.")
        invite = True  # an earlier invitation wasn't accepted: send a fresh one
    mm = MerchantMembership(merchant_id=access.merchant.id, user_id=user.id, role=body.role)
    db.add(mm)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise ApiError(409, "already_member", "They're already on this team.") from exc
    await db.refresh(mm)
    if invite:
        await _send_invite(db, user, access.merchant.trading_name)
    await audit.record(db, "team.member_added", actor_user_id=access.auth.user.id,
                       merchant_id=access.merchant.id, target_type="user",
                       target_id=str(user.id), ip=client_ip(request),
                       metadata={"role": body.role, "invited": invite})
    return member_out(mm, user)


async def _send_invite(db: AsyncSession, user: User, business: str) -> None:
    token = new_token()
    db.add(UserToken(user_id=user.id, purpose="invite", token_hash=token_hash(token),
                     expires_at=clock.now() + timedelta(days=INVITE_DAYS)))
    await db.flush()
    link = f"{get_settings().web_origin}/accept-invite?token={token}"
    send_email(user.email, f"You've been invited to {business} on STLL PAY",
               f"Set your password here (valid for {INVITE_DAYS} days): {link}")


async def _load_member(db: AsyncSession, merchant_id: uuid.UUID,
                       membership_id: uuid.UUID) -> tuple[MerchantMembership, User]:
    row = (await db.execute(
        select(MerchantMembership, User).join(User, User.id == MerchantMembership.user_id)
        .where(MerchantMembership.id == membership_id,
               MerchantMembership.merchant_id == merchant_id)
    )).one_or_none()
    if row is None:
        raise not_found("Team member not found")
    return row[0], row[1]


def _owner_guard(exc: DBAPIError) -> ApiError:
    if "at least one owner" in str(exc.orig):
        return ApiError(409, "last_owner", "A business needs at least one owner.")
    raise exc


@router.patch("/{merchant_id}/members/{membership_id}")
async def update_member(membership_id: uuid.UUID, body: MemberUpdate, request: Request, db: Db,
                        access: MerchantAccess = Depends(
                            merchant_access(Perm.TEAM_MANAGE, step_up=True))) -> dict[str, Any]:
    mm, user = await _load_member(db, access.merchant.id, membership_id)
    old = mm.role
    mm.role = body.role
    try:
        await db.flush()
    except DBAPIError as exc:
        raise _owner_guard(exc) from exc
    await audit.record(db, "team.role_changed", actor_user_id=access.auth.user.id,
                       merchant_id=access.merchant.id, target_type="user",
                       target_id=str(user.id), ip=client_ip(request),
                       metadata={"from": old, "to": body.role})
    return member_out(mm, user)


@router.delete("/{merchant_id}/members/{membership_id}")
async def remove_member(membership_id: uuid.UUID, request: Request, db: Db,
                        access: MerchantAccess = Depends(
                            merchant_access(Perm.TEAM_MANAGE, step_up=True))) -> dict[str, bool]:
    mm, user = await _load_member(db, access.merchant.id, membership_id)
    await db.delete(mm)
    try:
        await db.flush()
    except DBAPIError as exc:
        raise _owner_guard(exc) from exc
    await audit.record(db, "team.member_removed", actor_user_id=access.auth.user.id,
                       merchant_id=access.merchant.id, target_type="user",
                       target_id=str(user.id), ip=client_ip(request))
    return {"ok": True}


@router.get("/{merchant_id}/audit")
async def merchant_audit(db: Db, access: MerchantAccess = Depends(merchant_access(Perm.AUDIT_READ))
                         ) -> dict[str, Any]:
    rows = (await db.execute(
        select(AuditLog).where(AuditLog.merchant_id == access.merchant.id)
        .order_by(AuditLog.id.desc()).limit(100)
    )).scalars().all()
    return {"items": [
        {"id": r.id, "created_at": r.created_at, "action": r.action,
         "actor_user_id": str(r.actor_user_id) if r.actor_user_id else None,
         "target_type": r.target_type, "target_id": r.target_id, "metadata": r.metadata_}
        for r in rows
    ]}
