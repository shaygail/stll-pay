"""Merchant dashboard endpoints for payments. All scoped to one merchant with RBAC."""

from __future__ import annotations

import ipaddress
import re
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import MerchantAccess, client_ip, merchant_access
from app.core import audit, clock
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import ApiError, not_found
from app.core.ids import random_code
from app.core.rbac import Perm
from app.core.security import encrypt_field, token_hash
from app.payments import service
from app.payments.models import (
    ApiKey,
    BankAccount,
    PaymentAttempt,
    PaymentLink,
    PaymentRequest,
    PosIntegration,
    Reconciliation,
    StaticQr,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.payments.providers.registry import get_provider

router = APIRouter(prefix="/v1/merchants/{merchant_id}", tags=["payments"])
Db = Annotated[AsyncSession, Depends(get_db)]

NZ_ACCOUNT = re.compile(r"^(\d{2})-?(\d{4})-?(\d{7,8})-?(\d{2,3})$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def normalise_account(raw: str) -> str:
    m = NZ_ACCOUNT.match(raw.replace(" ", ""))
    if not m:
        raise ApiError(422, "invalid_account_number",
                       "Enter a NZ bank account number, like 12-3456-7890123-00.")
    return "-".join(m.groups())


def money_out(attempt: PaymentAttempt) -> dict[str, Any]:
    return {
        "id": str(attempt.id), "status": attempt.status, "reason": attempt.status_reason,
        "provider": attempt.provider, "live": attempt.live,
        "provider_status": attempt.provider_status,
        "provider_status_code": attempt.provider_status_code,
        "destination": f"{attempt.destination_name} ··{attempt.destination_last4}",
        "created_at": attempt.created_at, "authorised_at": attempt.authorised_at,
        "sent_at": attempt.sent_at, "terminal_at": attempt.terminal_at,
    }


# ---------------------------------------------------------------- bank accounts


class BankAccountIn(Strict):
    account_number: str = Field(min_length=15, max_length=22)
    holder_name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9 '\.,&\-_]+$")


def account_out(a: BankAccount) -> dict[str, Any]:
    return {"id": str(a.id), "holder_name": a.holder_name, "last4": a.account_last4,
            "status": a.status, "created_at": a.created_at, "verified_at": a.verified_at}


@router.get("/bank-accounts")
async def list_bank_accounts(db: Db, access: MerchantAccess = Depends(
        merchant_access(Perm.MERCHANT_READ))) -> dict[str, Any]:
    rows = (await db.execute(select(BankAccount).where(
        BankAccount.merchant_id == access.merchant.id).order_by(BankAccount.created_at.desc())
    )).scalars().all()
    return {"items": [account_out(a) for a in rows]}


@router.post("/bank-accounts", status_code=201)
async def add_bank_account(body: BankAccountIn, request: Request, db: Db,
                           access: MerchantAccess = Depends(
                               merchant_access(Perm.BANK_MANAGE, step_up=True))) -> dict[str, Any]:
    number = normalise_account(body.account_number)
    acct = BankAccount(merchant_id=access.merchant.id, account_number_enc=encrypt_field(number),
                       account_last4=re.sub(r"\D", "", number)[-4:], holder_name=body.holder_name,
                       created_by=access.auth.user.id)
    db.add(acct)
    await db.flush()
    await db.refresh(acct)
    await audit.record(db, "bank_account.added", actor_user_id=access.auth.user.id,
                       merchant_id=access.merchant.id, target_type="bank_account",
                       target_id=str(acct.id), ip=client_ip(request),
                       metadata={"last4": acct.account_last4})
    return account_out(acct)


# ---------------------------------------------------------------- static QR codes


class QrIn(Strict):
    label: str = Field(min_length=1, max_length=80)


class QrUpdate(Strict):
    active: bool


def qr_out(q: StaticQr, slug: str) -> dict[str, Any]:
    return {"id": str(q.id), "label": q.label, "code": q.code, "active": q.active,
            "url": f"{get_settings().web_origin}/pay/{slug}?qr={q.code}",
            "created_at": q.created_at}


@router.get("/qr-codes")
async def list_qr(db: Db, access: MerchantAccess = Depends(merchant_access(Perm.MERCHANT_READ))
                  ) -> dict[str, Any]:
    rows = (await db.execute(select(StaticQr).where(StaticQr.merchant_id == access.merchant.id)
                             .order_by(StaticQr.created_at))).scalars().all()
    return {"items": [qr_out(q, access.merchant.slug) for q in rows]}


@router.post("/qr-codes", status_code=201)
async def create_qr(body: QrIn, db: Db, access: MerchantAccess = Depends(
        merchant_access(Perm.MERCHANT_UPDATE))) -> dict[str, Any]:
    q = StaticQr(merchant_id=access.merchant.id, code=random_code(8), label=body.label,
                 created_by=access.auth.user.id)
    db.add(q)
    await db.flush()
    await db.refresh(q)
    await audit.record(db, "qr.created", actor_user_id=access.auth.user.id,
                       merchant_id=access.merchant.id, target_type="static_qr", target_id=str(q.id))
    return qr_out(q, access.merchant.slug)


@router.patch("/qr-codes/{qr_id}")
async def update_qr(qr_id: uuid.UUID, body: QrUpdate, db: Db, access: MerchantAccess = Depends(
        merchant_access(Perm.MERCHANT_UPDATE))) -> dict[str, Any]:
    q = (await db.execute(select(StaticQr).where(StaticQr.id == qr_id,
                                                 StaticQr.merchant_id == access.merchant.id))
         ).scalar_one_or_none()
    if q is None:
        raise not_found("QR code not found")
    q.active = body.active
    await db.flush()
    return qr_out(q, access.merchant.slug)


# ---------------------------------------------------------------- payment links


class LinkIn(Strict):
    amount_cents: StrictInt | None = Field(default=None, gt=0)
    description: str = Field(min_length=1, max_length=140)
    order_ref: str | None = Field(default=None, max_length=60)
    expires_at: datetime | None = None
    max_uses: StrictInt | None = Field(default=None, gt=0, le=10_000)


def link_out(link: PaymentLink) -> dict[str, Any]:
    expired = link.expires_at is not None and link.expires_at <= clock.now()
    return {"id": str(link.id), "code": link.code, "amount_cents": link.amount_cents,
            "description": link.description, "order_ref": link.order_ref,
            "expires_at": link.expires_at, "max_uses": link.max_uses,
            "paid_count": link.paid_count, "active": link.active and not expired,
            "url": f"{get_settings().web_origin}/l/{link.code}", "created_at": link.created_at}


@router.get("/payment-links")
async def list_links(db: Db, access: MerchantAccess = Depends(merchant_access(Perm.PAYMENTS_READ))
                     ) -> dict[str, Any]:
    rows = (await db.execute(select(PaymentLink).where(
        PaymentLink.merchant_id == access.merchant.id).order_by(PaymentLink.created_at.desc())
    )).scalars().all()
    return {"items": [link_out(link) for link in rows]}


@router.post("/payment-links", status_code=201)
async def create_link(body: LinkIn, db: Db, access: MerchantAccess = Depends(
        merchant_access(Perm.PAYMENTS_CREATE))) -> dict[str, Any]:
    if body.amount_cents is not None:
        service.check_amount(body.amount_cents)
    if body.expires_at is not None and body.expires_at <= clock.now():
        raise ApiError(422, "invalid_expiry", "The expiry must be in the future.")
    link = PaymentLink(merchant_id=access.merchant.id, code=random_code(14),
                       amount_cents=body.amount_cents, description=body.description,
                       order_ref=body.order_ref, expires_at=body.expires_at,
                       max_uses=body.max_uses, created_by=access.auth.user.id)
    db.add(link)
    await db.flush()
    await db.refresh(link)
    await audit.record(db, "payment_link.created", actor_user_id=access.auth.user.id,
                       merchant_id=access.merchant.id, target_type="payment_link",
                       target_id=str(link.id), metadata={"amount_cents": body.amount_cents})
    return link_out(link)


@router.post("/payment-links/{link_id}/disable")
async def disable_link(link_id: uuid.UUID, db: Db, access: MerchantAccess = Depends(
        merchant_access(Perm.PAYMENTS_CANCEL))) -> dict[str, Any]:
    link = (await db.execute(select(PaymentLink).where(
        PaymentLink.id == link_id, PaymentLink.merchant_id == access.merchant.id))
    ).scalar_one_or_none()
    if link is None:
        raise not_found("Payment link not found")
    link.active = False
    await db.flush()
    return link_out(link)


# ---------------------------------------------------------------- payment requests


class RequestIn(Strict):
    amount_cents: StrictInt
    description: str | None = Field(default=None, max_length=140)
    expires_in_seconds: StrictInt = Field(default=900, ge=60, le=86_400)


@router.get("/payment-requests")
async def list_requests(db: Db, access: MerchantAccess = Depends(
        merchant_access(Perm.PAYMENTS_READ)),
        status: Literal["open", "paid", "cancelled", "expired", "requires_review"] | None = None,
        limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    q = select(PaymentRequest).where(PaymentRequest.merchant_id == access.merchant.id)
    if status:
        q = q.where(PaymentRequest.status == status)
    rows = (await db.execute(q.order_by(PaymentRequest.created_at.desc()).limit(limit))
            ).scalars().all()
    return {"items": [service.request_public(r) for r in rows]}


@router.post("/payment-requests", status_code=201)
async def create_request(body: RequestIn, db: Db, access: MerchantAccess = Depends(
        merchant_access(Perm.PAYMENTS_CREATE))) -> dict[str, Any]:
    req = await service.create_request(
        db, access.merchant, get_provider(), source="dashboard", amount_cents=body.amount_cents,
        expires_in=timedelta(seconds=body.expires_in_seconds), description=body.description,
        created_by=access.auth.user.id)
    return service.request_public(req)


@router.get("/payment-requests/{request_id}")
async def get_request(request_id: uuid.UUID, db: Db, access: MerchantAccess = Depends(
        merchant_access(Perm.PAYMENTS_READ))) -> dict[str, Any]:
    req = (await db.execute(select(PaymentRequest).where(
        PaymentRequest.id == request_id, PaymentRequest.merchant_id == access.merchant.id))
    ).scalar_one_or_none()
    if req is None:
        raise not_found("Payment request not found")
    attempts = (await db.execute(select(PaymentAttempt).where(
        PaymentAttempt.payment_request_id == req.id).order_by(PaymentAttempt.created_at))
    ).scalars().all()
    history = (await db.execute(select(Reconciliation).where(
        Reconciliation.attempt_id.in_([a.id for a in attempts]))
        .order_by(Reconciliation.created_at))).scalars().all() if attempts else []
    return {**service.request_public(req),
            "attempts": [money_out(a) for a in attempts],
            "history": [{"attempt_id": str(h.attempt_id), "method": h.method,
                         "from": h.from_status, "to": h.to_status,
                         "provider_status": h.provider_status, "provider_code": h.provider_code,
                         "at": h.created_at} for h in history]}


@router.post("/payment-requests/{request_id}/cancel")
async def cancel_request(request_id: uuid.UUID, db: Db, access: MerchantAccess = Depends(
        merchant_access(Perm.PAYMENTS_CANCEL))) -> dict[str, Any]:
    exists = (await db.execute(select(PaymentRequest.id).where(
        PaymentRequest.id == request_id, PaymentRequest.merchant_id == access.merchant.id))
    ).scalar_one_or_none()
    if exists is None:
        raise not_found("Payment request not found")
    req = await service.cancel_request(access.merchant.id, request_id, get_provider(),
                                       access.auth.user.id)
    return service.request_public(req)


@router.get("/summary")
async def summary(db: Db, access: MerchantAccess = Depends(merchant_access(Perm.PAYMENTS_READ)),
                  days: int = Query(default=1, ge=1, le=90)) -> dict[str, Any]:
    """Received means confirmed by the bank (SENT). Processing is shown separately."""
    since = clock.now() - timedelta(days=days)
    rows = (await db.execute(text(
        "SELECT status, count(*), COALESCE(sum(amount_cents), 0) FROM payment_requests"
        " WHERE merchant_id = :m AND created_at >= :since GROUP BY status"),
        {"m": access.merchant.id, "since": since})).all()
    by = {r[0]: {"count": r[1], "amount_cents": int(r[2])} for r in rows}
    processing = (await db.execute(select(func.count(), func.coalesce(
        func.sum(PaymentAttempt.amount_cents), 0)).where(
        PaymentAttempt.merchant_id == access.merchant.id,
        PaymentAttempt.status.in_(["AUTHORISED", "PROCESSING"])))).one()
    empty = {"count": 0, "amount_cents": 0}
    return {
        "since": since, "test_mode": not get_provider().live,
        "received": by.get("paid", empty),
        "processing": {"count": processing[0], "amount_cents": int(processing[1])},
        "needs_review": by.get("requires_review", empty),
        "not_paid": {"count": sum(by.get(k, empty)["count"] for k in ("cancelled", "expired"))},
        "open": by.get("open", empty),
    }


# ---------------------------------------------------------------- integrations


class IntegrationIn(Strict):
    name: str = Field(min_length=1, max_length=80)


class IntegrationUpdate(Strict):
    enabled: bool


class ApiKeyIn(Strict):
    name: str = Field(min_length=1, max_length=80)


class WebhookIn(Strict):
    url: str = Field(min_length=8, max_length=500)


def integration_out(i: PosIntegration) -> dict[str, Any]:
    return {"id": str(i.id), "name": i.name, "enabled": i.enabled, "created_at": i.created_at}


async def _integration(db: AsyncSession, access: MerchantAccess,
                       integration_id: uuid.UUID) -> PosIntegration:
    i = (await db.execute(select(PosIntegration).where(
        PosIntegration.id == integration_id, PosIntegration.merchant_id == access.merchant.id))
    ).scalar_one_or_none()
    if i is None:
        raise not_found("Integration not found")
    return i


def check_webhook_url(url: str) -> None:
    parsed = urlparse(url)
    deployed = get_settings().is_deployed
    if parsed.scheme not in (("https",) if deployed else ("https", "http")) or not parsed.hostname:
        raise ApiError(422, "invalid_url", "Use an https:// address.")
    host = parsed.hostname.lower()
    if deployed:
        if host in ("localhost",) or host.endswith((".local", ".internal")):
            raise ApiError(422, "invalid_url", "That address isn't reachable from the internet.")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ApiError(422, "invalid_url", "That address isn't reachable from the internet.")


@router.get("/integrations")
async def list_integrations(db: Db, access: MerchantAccess = Depends(
        merchant_access(Perm.INTEGRATIONS_MANAGE))) -> dict[str, Any]:
    rows = (await db.execute(select(PosIntegration).where(
        PosIntegration.merchant_id == access.merchant.id).order_by(PosIntegration.created_at))
    ).scalars().all()
    out = []
    for i in rows:
        keys = (await db.execute(select(ApiKey).where(ApiKey.pos_integration_id == i.id))
                ).scalars().all()
        hooks = (await db.execute(select(WebhookEndpoint).where(
            WebhookEndpoint.pos_integration_id == i.id))).scalars().all()
        out.append({**integration_out(i),
                    "api_keys": [{"id": str(k.id), "name": k.name, "prefix": k.prefix,
                                  "created_at": k.created_at, "last_used_at": k.last_used_at,
                                  "revoked_at": k.revoked_at} for k in keys],
                    "webhook_endpoints": [{"id": str(h.id), "url": h.url, "enabled": h.enabled}
                                          for h in hooks]})
    return {"items": out}


@router.post("/integrations", status_code=201)
async def create_integration(body: IntegrationIn, db: Db, access: MerchantAccess = Depends(
        merchant_access(Perm.INTEGRATIONS_MANAGE, step_up=True))) -> dict[str, Any]:
    i = PosIntegration(merchant_id=access.merchant.id, name=body.name,
                       created_by=access.auth.user.id)
    db.add(i)
    await db.flush()
    await db.refresh(i)
    await audit.record(db, "integration.created", actor_user_id=access.auth.user.id,
                       merchant_id=access.merchant.id, target_type="pos_integration",
                       target_id=str(i.id))
    return integration_out(i)


@router.patch("/integrations/{integration_id}")
async def update_integration(integration_id: uuid.UUID, body: IntegrationUpdate, db: Db,
                             access: MerchantAccess = Depends(
                                 merchant_access(Perm.INTEGRATIONS_MANAGE))) -> dict[str, Any]:
    i = await _integration(db, access, integration_id)
    i.enabled = body.enabled
    await db.flush()
    await audit.record(db, "integration.enabled" if body.enabled else "integration.disabled",
                       actor_user_id=access.auth.user.id, merchant_id=access.merchant.id,
                       target_type="pos_integration", target_id=str(i.id))
    return integration_out(i)


@router.post("/integrations/{integration_id}/api-keys", status_code=201)
async def create_api_key(integration_id: uuid.UUID, body: ApiKeyIn, db: Db,
                         access: MerchantAccess = Depends(
                             merchant_access(Perm.INTEGRATIONS_MANAGE, step_up=True))
                         ) -> dict[str, Any]:
    i = await _integration(db, access, integration_id)
    env = "live" if get_settings().environment == "production" else "test"
    prefix = f"sp_{env}_{random_code(8).lower()}"
    key = f"{prefix}_{secrets.token_urlsafe(32)}"
    k = ApiKey(merchant_id=access.merchant.id, pos_integration_id=i.id, name=body.name,
               prefix=prefix, key_hash=token_hash(key), created_by=access.auth.user.id)
    db.add(k)
    await db.flush()
    await audit.record(db, "api_key.created", actor_user_id=access.auth.user.id,
                       merchant_id=access.merchant.id, target_type="api_key",
                       target_id=str(k.id), metadata={"prefix": prefix})
    # The full key is shown once and never stored.
    return {"id": str(k.id), "name": k.name, "prefix": prefix, "key": key}


@router.post("/integrations/{integration_id}/api-keys/{key_id}/revoke")
async def revoke_api_key(integration_id: uuid.UUID, key_id: uuid.UUID, db: Db,
                         access: MerchantAccess = Depends(
                             merchant_access(Perm.INTEGRATIONS_MANAGE))) -> dict[str, Any]:
    await _integration(db, access, integration_id)
    k = (await db.execute(select(ApiKey).where(ApiKey.id == key_id,
                                               ApiKey.pos_integration_id == integration_id))
         ).scalar_one_or_none()
    if k is None:
        raise not_found("API key not found")
    k.revoked_at = k.revoked_at or clock.now()
    await db.flush()
    await audit.record(db, "api_key.revoked", actor_user_id=access.auth.user.id,
                       merchant_id=access.merchant.id, target_type="api_key", target_id=str(k.id))
    return {"id": str(k.id), "revoked_at": k.revoked_at}


@router.post("/integrations/{integration_id}/webhook-endpoints", status_code=201)
async def create_webhook_endpoint(integration_id: uuid.UUID, body: WebhookIn, db: Db,
                                  access: MerchantAccess = Depends(
                                      merchant_access(Perm.INTEGRATIONS_MANAGE, step_up=True))
                                  ) -> dict[str, Any]:
    i = await _integration(db, access, integration_id)
    check_webhook_url(body.url)
    secret = "whsec_" + secrets.token_urlsafe(32)
    ep = WebhookEndpoint(merchant_id=access.merchant.id, pos_integration_id=i.id, url=body.url,
                         secret_enc=encrypt_field(secret))
    db.add(ep)
    await db.flush()
    await audit.record(db, "webhook_endpoint.created", actor_user_id=access.auth.user.id,
                       merchant_id=access.merchant.id, target_type="webhook_endpoint",
                       target_id=str(ep.id), metadata={"url": body.url})
    return {"id": str(ep.id), "url": ep.url, "signing_secret": secret}


@router.get("/webhook-deliveries")
async def list_deliveries(db: Db, access: MerchantAccess = Depends(
        merchant_access(Perm.INTEGRATIONS_MANAGE))) -> dict[str, Any]:
    rows = (await db.execute(select(WebhookDelivery).where(
        WebhookDelivery.merchant_id == access.merchant.id)
        .order_by(WebhookDelivery.created_at.desc()).limit(100))).scalars().all()
    return {"items": [{"id": str(d.id), "event_id": str(d.event_id), "event_type": d.event_type,
                       "status": d.status, "attempts": d.attempts,
                       "last_status_code": d.last_status_code, "created_at": d.created_at,
                       "delivered_at": d.delivered_at} for d in rows]}
