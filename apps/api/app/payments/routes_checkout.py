"""Public checkout endpoints (no login). Each call resolves a public identifier to a single
merchant and then works only inside that merchant's row-level security context.

None of these endpoints can mark a payment as paid. The return page only reads status;
"paid" comes from verified provider information.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import client_ip
from app.core import clock
from app.core.config import get_settings
from app.core.db import get_db, set_context
from app.core.errors import ApiError, not_found
from app.core.ratelimit import limiter
from app.models import Merchant
from app.payments import service
from app.payments.models import PaymentAttempt, PaymentLink, PaymentRequest, StaticQr
from app.payments.providers.base import ProviderError
from app.payments.providers.registry import get_mock_provider, get_provider
from app.payments.states import CUSTOMER_VIEW, IN_FLIGHT, Status

router = APIRouter(prefix="/v1/checkout", tags=["checkout"])
mock_bank = APIRouter(prefix="/v1/mock-bank", tags=["mock bank (development only)"])
Db = Annotated[AsyncSession, Depends(get_db)]

STATIC_QR_EXPIRY = timedelta(minutes=30)
LINK_REQUEST_EXPIRY = timedelta(minutes=30)
REFRESH_MIN_INTERVAL = timedelta(seconds=3)


class AmountIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    amount_cents: StrictInt | None = None
    note: str | None = Field(default=None, max_length=140)
    qr: str | None = Field(default=None, pattern=r"^[0-9A-Z]{8}$")


async def _merchant_by_slug(db: AsyncSession, slug: str) -> Merchant:
    merchant_id = (await db.execute(text("SELECT lookup_merchant_by_slug(:s)"),
                                    {"s": slug.lower()})).scalar()
    if merchant_id is None:
        raise not_found("We couldn't find that business.")
    await set_context(db, merchant_id=merchant_id, is_platform_admin=False)
    return (await db.execute(select(Merchant).where(Merchant.id == merchant_id))).scalar_one()


def _merchant_public(m: Merchant, accepting: bool) -> dict[str, Any]:
    return {"trading_name": m.trading_name, "slug": m.slug, "verified": m.status == "active",
            "accepting_payments": accepting, "test_mode": not get_provider().live}


# ---------------------------------------------------------------- counter QR


@router.get("/merchants/{slug}")
async def merchant_page(slug: str, db: Db) -> dict[str, Any]:
    m = await _merchant_by_slug(db, slug)
    return _merchant_public(m, await service.verified_account(db, m.id) is not None)


@router.post("/merchants/{slug}/requests", status_code=201)
async def counter_request(slug: str, body: AmountIn, request: Request, db: Db) -> dict[str, str]:
    limiter.check(f"checkout:create:{client_ip(request)}", 20, 600)
    m = await _merchant_by_slug(db, slug)
    if body.amount_cents is None:
        raise ApiError(422, "invalid_amount", "Enter an amount.")
    qr_id = None
    if body.qr:
        qr = (await db.execute(select(StaticQr).where(StaticQr.code == body.qr,
                                                      StaticQr.merchant_id == m.id))
              ).scalar_one_or_none()
        if qr is None or not qr.active:
            raise ApiError(409, "qr_inactive", "This QR code is no longer in use.")
        qr_id = qr.id
    req = await service.create_request(db, m, get_provider(), source="static_qr",
                                       amount_cents=body.amount_cents, expires_in=STATIC_QR_EXPIRY,
                                       description=body.note, static_qr_id=qr_id)
    return {"checkout_token": req.checkout_token}


# ---------------------------------------------------------------- payment links


async def _link(db: AsyncSession, code: str) -> tuple[PaymentLink, Merchant]:
    merchant_id = (await db.execute(text("SELECT lookup_link_by_code(:c)"), {"c": code})).scalar()
    if merchant_id is None:
        raise not_found("This payment link doesn't exist.")
    await set_context(db, merchant_id=merchant_id, is_platform_admin=False)
    link = await service.link_for_code(db, code)
    m = (await db.execute(select(Merchant).where(Merchant.id == merchant_id))).scalar_one()
    assert link is not None
    return link, m


def _link_state(link: PaymentLink) -> str:
    if not link.active:
        return "disabled"
    if link.expires_at is not None and link.expires_at <= clock.now():
        return "expired"
    if link.max_uses is not None and link.paid_count >= link.max_uses:
        return "used"
    return "active"


@router.get("/links/{code}")
async def link_page(code: str, db: Db) -> dict[str, Any]:
    link, m = await _link(db, code)
    return {"merchant": _merchant_public(m, True), "description": link.description,
            "order_ref": link.order_ref, "amount_cents": link.amount_cents,
            "state": _link_state(link), "expires_at": link.expires_at}


@router.post("/links/{code}/requests", status_code=201)
async def link_request(code: str, body: AmountIn, request: Request, db: Db) -> dict[str, str]:
    limiter.check(f"checkout:create:{client_ip(request)}", 20, 600)
    link, m = await _link(db, code)
    state = _link_state(link)
    if state != "active":
        raise ApiError(409, f"link_{state}", {
            "disabled": "This payment link has been switched off.",
            "expired": "This payment link has expired.",
            "used": "This payment link has already been paid."}[state])
    if link.amount_cents is not None:
        # Fixed-amount link: the amount comes from the link, never from the customer.
        if body.amount_cents is not None and body.amount_cents != link.amount_cents:
            raise ApiError(422, "invalid_amount", "This link is for a fixed amount.")
        amount = link.amount_cents
    else:
        if body.amount_cents is None:
            raise ApiError(422, "invalid_amount", "Enter an amount.")
        amount = body.amount_cents
    expires = LINK_REQUEST_EXPIRY
    if link.expires_at is not None:
        expires = min(expires, link.expires_at - clock.now())
    req = await service.create_request(db, m, get_provider(), source="link", amount_cents=amount,
                                       expires_in=expires, description=link.description,
                                       payment_link_id=link.id)
    return {"checkout_token": req.checkout_token}


# ---------------------------------------------------------------- checkout


async def _request(db: AsyncSession, token: str) -> tuple[PaymentRequest, Merchant]:
    merchant_id = (await db.execute(text("SELECT lookup_request_by_token(:t)"), {"t": token}
                                    )).scalar()
    if merchant_id is None:
        raise not_found("This payment link isn't valid.")
    await set_context(db, merchant_id=merchant_id, is_platform_admin=False)
    req = (await db.execute(select(PaymentRequest).where(PaymentRequest.checkout_token == token))
           ).scalar_one()
    m = (await db.execute(select(Merchant).where(Merchant.id == merchant_id))).scalar_one()
    return req, m


async def _latest_attempt(db: AsyncSession, req: PaymentRequest) -> PaymentAttempt | None:
    return (await db.execute(select(PaymentAttempt).where(
        PaymentAttempt.payment_request_id == req.id).order_by(PaymentAttempt.created_at.desc())
        .limit(1))).scalar_one_or_none()


def customer_state(req: PaymentRequest, attempt: PaymentAttempt | None) -> str:
    """One word the checkout page shows. 'paid' only when the provider confirmed SENT."""
    if req.status == "paid":
        return "paid"
    if req.status == "requires_review":
        return "unconfirmed"
    if attempt is not None and Status(attempt.status) in IN_FLIGHT | {Status.SENT}:
        return CUSTOMER_VIEW[Status(attempt.status)]
    if req.status in ("cancelled", "expired"):
        return req.status
    if req.status == "open" and req.expires_at <= clock.now():
        return "expired"
    if attempt is not None and attempt.status in (Status.FAILED, Status.CANCELLED,
                                                  Status.EXPIRED):
        return {"FAILED": "failed", "CANCELLED": "cancelled", "EXPIRED": "ready"}[attempt.status]
    return "ready"


@router.get("/requests/{token}")
async def checkout_view(token: str, request: Request, db: Db, refresh: bool = False
                        ) -> dict[str, Any]:
    req, m = await _request(db, token)
    attempt = await _latest_attempt(db, req)
    if (refresh and attempt is not None and attempt.provider_payment_id
            and attempt.status in (Status.AWAITING_AUTHORISATION, Status.AUTHORISED,
                                   Status.PROCESSING)
            and (attempt.last_polled_at is None
                 or clock.now() - attempt.last_polled_at >= REFRESH_MIN_INTERVAL)):
        # Ask the provider (the source of truth), not the browser.
        limiter.check(f"checkout:refresh:{client_ip(request)}", 120, 60)
        await service.refresh_attempt(m.id, attempt.id, get_provider(),
                                      method="checkout_refresh")
        db.expire_all()
        req, m = await _request(db, token)
        attempt = await _latest_attempt(db, req)
    state = customer_state(req, attempt)
    return {
        "merchant": _merchant_public(m, True),
        "amount_cents": req.amount_cents, "currency": req.currency,
        "description": req.description, "reference": req.reference,
        "source": req.source, "expires_at": req.expires_at,
        "state": state,
        "can_pay": state in ("ready", "failed", "cancelled") and req.status == "open"
        and req.expires_at > clock.now(),
        "resume_url": attempt.authorisation_url if attempt is not None
        and attempt.status == Status.AWAITING_AUTHORISATION else None,
        "paid_at": req.paid_at,
    }


@router.post("/requests/{token}/pay")
async def checkout_pay(token: str, request: Request, db: Db) -> dict[str, str]:
    limiter.check(f"checkout:pay:{client_ip(request)}", 30, 600)
    _req, m = await _request(db, token)
    result = await service.start_attempt(m.id, token, get_provider())
    return {"authorisation_url": result.authorisation_url}


# ---------------------------------------------------------------- mock bank (dev and test only)


def _mock() -> Any:
    mock = get_mock_provider()
    if mock is None or get_settings().environment == "production":
        raise not_found()
    return mock


@mock_bank.get("/{payment_id}")
async def mock_bank_view(payment_id: str) -> dict[str, Any]:
    row = await _mock().bank_view(payment_id)
    if row is None:
        raise not_found()
    return {"id": row["id"], "amount_cents": row["amount_cents"], "payee_name": row["payee_name"],
            "payee_reference": row["payee_reference"], "status": row["status"],
            "status_code": row["status_code"], "expires_at": row["expires_at"]}


@mock_bank.post("/{payment_id}/approve")
async def mock_bank_approve(payment_id: str) -> dict[str, str]:
    try:
        return {"redirect_uri": await _mock().customer_approves(payment_id)}
    except ProviderError as exc:
        raise ApiError(409, exc.code, str(exc)) from exc


@mock_bank.post("/{payment_id}/cancel")
async def mock_bank_cancel(payment_id: str) -> dict[str, str]:
    try:
        return {"redirect_uri": await _mock().customer_cancels(payment_id)}
    except ProviderError as exc:
        raise ApiError(409, exc.code, str(exc)) from exc

