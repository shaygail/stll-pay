"""Platform administration. Admins need MFA within the last 2 hours for every request."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import AuthContext, client_ip, platform_admin
from app.core import audit
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import ApiError, not_found
from app.merchants.routes import merchant_out
from app.models import Merchant

router = APIRouter(prefix="/v1/admin", tags=["admin"])
Db = Annotated[AsyncSession, Depends(get_db)]

MerchantStatus = Literal["draft", "pending_review", "restricted", "active", "suspended"]

# Allowed approval transitions. Everything else is refused.
TRANSITIONS: dict[str, set[str]] = {
    "draft": {"pending_review"},
    "pending_review": {"draft", "restricted", "active"},
    "restricted": {"active", "suspended"},
    "active": {"restricted", "suspended"},
    "suspended": {"restricted", "active"},
}


class StatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: MerchantStatus
    reason: str = Field(min_length=3, max_length=500)


class LiveChange(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    enabled: bool
    reason: str = Field(min_length=3, max_length=500)


async def _merchant(db: AsyncSession, merchant_id: uuid.UUID) -> Merchant:
    m = (await db.execute(select(Merchant).where(Merchant.id == merchant_id))).scalar_one_or_none()
    if m is None:
        raise not_found("Merchant not found")
    return m


@router.get("/merchants")
async def list_merchants(db: Db, _: AuthContext = Depends(platform_admin),
                         status: MerchantStatus | None = Query(default=None)) -> dict[str, Any]:
    q = select(Merchant).where(Merchant.deleted_at.is_(None)).order_by(Merchant.created_at.desc())
    if status:
        q = q.where(Merchant.status == status)
    rows = (await db.execute(q.limit(200))).scalars().all()
    return {"items": [merchant_out(m) for m in rows]}


@router.post("/merchants/{merchant_id}/status")
async def change_status(merchant_id: uuid.UUID, body: StatusChange, request: Request, db: Db,
                        auth: AuthContext = Depends(platform_admin)) -> dict[str, Any]:
    m = await _merchant(db, merchant_id)
    if body.status not in TRANSITIONS.get(m.status, set()):
        raise ApiError(409, "invalid_transition",
                       f"A {m.status} business can't move to {body.status}.")
    old = m.status
    m.status = body.status
    if body.status != "active":
        m.live_enabled = False
    await db.flush()
    await audit.record(db, "admin.merchant_status", actor_user_id=auth.user.id,
                       merchant_id=m.id, target_type="merchant", target_id=str(m.id),
                       ip=client_ip(request),
                       metadata={"from": old, "to": body.status, "reason": body.reason})
    return merchant_out(m)


@router.post("/merchants/{merchant_id}/live")
async def change_live(merchant_id: uuid.UUID, body: LiveChange, request: Request, db: Db,
                      auth: AuthContext = Depends(platform_admin)) -> dict[str, Any]:
    m = await _merchant(db, merchant_id)
    if body.enabled:
        if not get_settings().live_payments_enabled:
            raise ApiError(409, "live_payments_off",
                           "Live payments are switched off for this environment.")
        if m.status != "active":
            raise ApiError(409, "merchant_not_active", "Only an active business can go live.")
    m.live_enabled = body.enabled
    await db.flush()
    await audit.record(db, "admin.merchant_live", actor_user_id=auth.user.id, merchant_id=m.id,
                       target_type="merchant", target_id=str(m.id), ip=client_ip(request),
                       metadata={"enabled": body.enabled, "reason": body.reason})
    return merchant_out(m)


@router.get("/audit/verify")
async def verify_audit_chain(db: Db, _: AuthContext = Depends(platform_admin)) -> dict[str, Any]:
    broken = (await db.execute(text("SELECT audit_verify_chain()"))).scalar()
    return {"intact": broken is None, "first_broken_id": broken}
