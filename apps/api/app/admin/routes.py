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


# ---------------------------------------------------------------- payments administration


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=3, max_length=500)


class Resolution(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    outcome: Literal["sent", "failed"]
    evidence: str = Field(min_length=10, max_length=1000)


@router.post("/bank-accounts/{account_id}/verify")
async def verify_bank_account(account_id: uuid.UUID, body: Decision, request: Request, db: Db,
                              auth: AuthContext = Depends(platform_admin)) -> dict[str, Any]:
    """Phase 6 adds the full ownership check. Until then, an admin confirms ownership
    out of band (for example a bank statement) and records how in the reason."""
    from app.payments.models import BankAccount
    from app.payments.routes_merchant import account_out

    acct = (await db.execute(select(BankAccount).where(BankAccount.id == account_id)
                             .with_for_update())).scalar_one_or_none()
    if acct is None:
        raise not_found("Bank account not found")
    if acct.status != "pending":
        raise ApiError(409, "invalid_status", f"This account is {acct.status}.")
    previous = (await db.execute(select(BankAccount).where(
        BankAccount.merchant_id == acct.merchant_id, BankAccount.status == "verified")
        .with_for_update())).scalars().all()
    from app.core import clock

    for p in previous:
        p.status = "retired"
        p.retired_at = clock.now()
    await db.flush()
    acct.status = "verified"
    acct.verified_by = auth.user.id
    acct.verified_at = clock.now()
    await db.flush()
    await audit.record(db, "admin.bank_account_verified", actor_user_id=auth.user.id,
                       merchant_id=acct.merchant_id, target_type="bank_account",
                       target_id=str(acct.id), ip=client_ip(request),
                       metadata={"reason": body.reason, "retired": [str(p.id) for p in previous]})
    return account_out(acct)


@router.get("/payments/review")
async def review_queue(db: Db, _: AuthContext = Depends(platform_admin)) -> dict[str, Any]:
    from app.payments.models import PaymentAttempt, PaymentRequest
    from app.payments.service import request_public

    rows = (await db.execute(select(PaymentRequest).where(
        PaymentRequest.status == "requires_review").order_by(PaymentRequest.updated_at.desc())
        .limit(200))).scalars().all()
    items = []
    for r in rows:
        attempts = (await db.execute(select(PaymentAttempt).where(
            PaymentAttempt.payment_request_id == r.id).order_by(PaymentAttempt.created_at))
        ).scalars().all()
        items.append({**request_public(r), "merchant_id": str(r.merchant_id),
                      "attempts": [{"id": str(a.id), "status": a.status,
                                    "reason": a.status_reason,
                                    "provider_status": a.provider_status,
                                    "provider_status_code": a.provider_status_code}
                                   for a in attempts]})
    return {"items": items}


@router.post("/payment-attempts/{attempt_id}/resolve")
async def resolve_attempt(attempt_id: uuid.UUID, body: Resolution, request: Request, db: Db,
                          auth: AuthContext = Depends(platform_admin)) -> dict[str, Any]:
    """Settle a payment the provider couldn't confirm, using evidence such as the merchant's
    bank statement. Only for attempts in REQUIRES_REVIEW; every resolution is audited."""
    from app.payments import service
    from app.payments.models import PaymentAttempt
    from app.payments.states import Status

    a = (await db.execute(select(PaymentAttempt).where(PaymentAttempt.id == attempt_id)
                          .with_for_update())).scalar_one_or_none()
    if a is None:
        raise not_found("Payment attempt not found")
    if a.status != Status.REQUIRES_REVIEW:
        raise ApiError(409, "not_in_review", "Only payments needing review can be resolved.")
    target = Status.SENT if body.outcome == "sent" else Status.FAILED
    await service._transition(db, a, target, method="admin_resolution",
                              reason="admin_resolution", operator_id=auth.user.id,
                              note=body.evidence)
    await audit.record(db, "admin.payment_resolved", actor_user_id=auth.user.id,
                       merchant_id=a.merchant_id, target_type="payment_attempt",
                       target_id=str(a.id), ip=client_ip(request),
                       metadata={"outcome": body.outcome, "evidence": body.evidence})
    return {"id": str(a.id), "status": a.status}
