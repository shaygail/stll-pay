"""Versioned POS integration API (STLL POS and other systems).

Authentication: ``Authorization: Bearer sp_test_xxxxxxxx_...`` (an API key from the
merchant dashboard). Creating a request needs an ``Idempotency-Key`` header: repeating a
call with the same key and body returns the same request instead of creating another.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.db import get_db, set_context
from app.core.errors import ApiError, not_found
from app.core.ratelimit import limiter
from app.core.security import constant_time_equals, token_hash
from app.models import Merchant
from app.payments import service
from app.payments.models import ApiKey, PaymentRequest, PosIntegration
from app.payments.providers.registry import get_provider

router = APIRouter(prefix="/v1/pos", tags=["pos"])
Db = Annotated[AsyncSession, Depends(get_db)]


@dataclass
class PosAuth:
    merchant: Merchant
    integration: PosIntegration
    key: ApiKey


def _invalid() -> ApiError:
    return ApiError(401, "invalid_api_key", "Invalid or revoked API key.")


async def pos_auth(db: Db, authorization: str | None = Header(default=None)) -> PosAuth:
    if not authorization or not authorization.startswith("Bearer "):
        raise _invalid()
    key = authorization.removeprefix("Bearer ").strip()
    parts = key.split("_")
    if len(parts) < 4 or parts[0] != "sp" or parts[1] not in ("test", "live"):
        raise _invalid()
    prefix = "_".join(parts[:3])
    limiter.check(f"pos:{prefix}", 600, 60)
    merchant_id = (await db.execute(text("SELECT lookup_api_key(:p)"), {"p": prefix})).scalar()
    if merchant_id is None:
        raise _invalid()
    await set_context(db, merchant_id=merchant_id, is_platform_admin=False)
    row = (await db.execute(select(ApiKey).where(ApiKey.prefix == prefix))).scalar_one_or_none()
    now = clock.now()
    if (row is None or not constant_time_equals(row.key_hash.hex(), token_hash(key).hex())
            or row.revoked_at is not None or (row.expires_at and row.expires_at <= now)):
        raise _invalid()
    integration = (await db.execute(select(PosIntegration).where(
        PosIntegration.id == row.pos_integration_id))).scalar_one()
    if not integration.enabled:
        raise ApiError(403, "integration_disabled", "This integration is switched off.")
    merchant = (await db.execute(select(Merchant).where(Merchant.id == merchant_id))).scalar_one()
    if row.last_used_at is None or now - row.last_used_at > timedelta(minutes=1):
        row.last_used_at = now
    return PosAuth(merchant, integration, row)


class PosRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    external_order_id: str = Field(min_length=1, max_length=100)
    amount_cents: StrictInt
    currency: str = Field(default="NZD", pattern="^NZD$")
    description: str | None = Field(default=None, max_length=140)
    expires_in_seconds: StrictInt = Field(default=900, ge=60, le=3600)


async def _own_request(db: AsyncSession, auth: PosAuth, request_id: uuid.UUID) -> PaymentRequest:
    req = (await db.execute(select(PaymentRequest).where(
        PaymentRequest.id == request_id, PaymentRequest.merchant_id == auth.merchant.id,
        PaymentRequest.pos_integration_id == auth.integration.id))).scalar_one_or_none()
    if req is None:
        raise not_found("Payment request not found")
    return req


@router.post("/payment-requests", status_code=201)
async def create_payment_request(
    body: PosRequestIn, request: Request, db: Db, auth: PosAuth = Depends(pos_auth),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    if not idempotency_key or not (1 <= len(idempotency_key) <= 200):
        raise ApiError(400, "idempotency_key_required", "Send an Idempotency-Key header.")
    body_hash = service.request_hash(body.model_dump())

    async def existing_by_key() -> PaymentRequest | None:
        return (await db.execute(select(PaymentRequest).where(
            PaymentRequest.merchant_id == auth.merchant.id,
            PaymentRequest.idempotency_key == idempotency_key))).scalar_one_or_none()

    prior = await existing_by_key()
    if prior is not None:
        if prior.request_hash != body_hash:
            raise ApiError(409, "idempotency_conflict",
                           "This Idempotency-Key was already used with different details.")
        return service.request_public(prior)

    try:
        async with db.begin_nested():
            req = await service.create_request(
                db, auth.merchant, get_provider(), source="dynamic_qr",
                amount_cents=body.amount_cents,
                expires_in=timedelta(seconds=body.expires_in_seconds),
                description=body.description, pos_integration_id=auth.integration.id,
                external_order_id=body.external_order_id, idempotency_key=idempotency_key,
                body_hash=body_hash)
    except IntegrityError as exc:
        prior = await existing_by_key()  # a concurrent identical call won the race
        if prior is not None and prior.request_hash == body_hash:
            return service.request_public(prior)
        open_one = (await db.execute(select(PaymentRequest).where(
            PaymentRequest.merchant_id == auth.merchant.id,
            PaymentRequest.pos_integration_id == auth.integration.id,
            PaymentRequest.external_order_id == body.external_order_id,
            PaymentRequest.status.in_(["open", "paid", "requires_review"])))).scalar_one_or_none()
        if open_one is not None:
            raise ApiError(409, "order_has_payment_request",
                           "This order already has an open or paid payment request.",
                           {"payment_request_id": str(open_one.id), "status": open_one.status}
                           ) from exc
        raise
    return service.request_public(req)


@router.get("/payment-requests/{request_id}")
async def get_payment_request(request_id: uuid.UUID, db: Db,
                              auth: PosAuth = Depends(pos_auth)) -> dict[str, Any]:
    return service.request_public(await _own_request(db, auth, request_id))


@router.get("/payment-requests")
async def find_by_order(external_order_id: str, db: Db,
                        auth: PosAuth = Depends(pos_auth)) -> dict[str, Any]:
    rows = (await db.execute(select(PaymentRequest).where(
        PaymentRequest.merchant_id == auth.merchant.id,
        PaymentRequest.pos_integration_id == auth.integration.id,
        PaymentRequest.external_order_id == external_order_id)
        .order_by(PaymentRequest.created_at.desc()))).scalars().all()
    return {"items": [service.request_public(r) for r in rows]}


@router.post("/payment-requests/{request_id}/cancel")
async def cancel_payment_request(request_id: uuid.UUID, db: Db,
                                 auth: PosAuth = Depends(pos_auth)) -> dict[str, Any]:
    req = await _own_request(db, auth, request_id)
    # The cancel flow runs in its own short transactions (no locks are held here).
    result = await service.cancel_request(auth.merchant.id, req.id, get_provider(), None)
    return service.request_public(result)
