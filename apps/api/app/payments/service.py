"""Payment engine: requests, attempts, provider updates and outcome events.

Each step runs in its own short transaction with the merchant's tenant context, and
always locks rows (``FOR UPDATE``) before changing them. Calls to the provider happen
outside database transactions, so a slow provider never holds locks; the attempt row
records progress, so a crash at any point leaves a state the worker can finish safely.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit, clock
from app.core.config import get_settings
from app.core.db import get_sessionmaker, set_context
from app.core.errors import ApiError
from app.core.ids import random_code, uuid7
from app.core.security import decrypt_field
from app.models import Merchant
from app.payments.models import (
    BankAccount,
    PaymentAttempt,
    PaymentLink,
    PaymentRequest,
    Reconciliation,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.payments.providers.base import (
    CreatePayment,
    ErrorKind,
    PaymentProvider,
    ProviderError,
    ProviderPayment,
)
from app.payments.states import COMMITTED, FINAL, IN_FLIGHT, SUCCESS, Decision, Status, decide

log = structlog.get_logger()

# Akahu requires a payment to stay open at least 15 minutes (docs/AKAHU_INTEGRATION.md §2).
PROVIDER_MIN_EXPIRY = timedelta(minutes=15, seconds=30)
POLL_SCHEDULE = [5, 10, 20, 30, 60, 120, 300]  # seconds between provider checks
STUCK_CREATED_AFTER = timedelta(minutes=5)
NO_RESULT_REVIEW_AFTER = timedelta(hours=24)


@asynccontextmanager
async def merchant_tx(merchant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """A transaction scoped to one merchant (row-level security applies)."""
    async with get_sessionmaker()() as s, s.begin():
        await set_context(s, merchant_id=merchant_id, is_platform_admin=False)
        yield s


@asynccontextmanager
async def system_tx() -> AsyncIterator[AsyncSession]:
    """A transaction for trusted background work across merchants (worker, webhooks)."""
    async with get_sessionmaker()() as s, s.begin():
        await set_context(s, is_platform_admin=True)
        yield s


def new_checkout_token() -> str:
    return secrets.token_urlsafe(24)


def new_reference() -> str:
    return "SP" + random_code(8)


def request_hash(body: dict[str, Any]) -> bytes:
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).digest()


# ---------------------------------------------------------------- eligibility


async def verified_account(s: AsyncSession, merchant_id: uuid.UUID) -> BankAccount | None:
    return (await s.execute(select(BankAccount).where(
        BankAccount.merchant_id == merchant_id, BankAccount.status == "verified"))
    ).scalar_one_or_none()


async def check_can_accept(s: AsyncSession, merchant: Merchant, provider: PaymentProvider) -> None:
    if merchant.deleted_at is not None or merchant.status not in ("restricted", "active"):
        raise ApiError(409, "merchant_not_accepting",
                       "This business isn't set up to accept payments yet.")
    if provider.live and not (merchant.live_enabled and merchant.status == "active"):
        raise ApiError(409, "merchant_not_live", "This business can't take live payments yet.")
    if await verified_account(s, merchant.id) is None:
        raise ApiError(409, "no_verified_account",
                       "This business has no verified bank account for payments.")


def check_amount(amount_cents: int) -> None:
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool):
        raise ApiError(422, "invalid_amount", "Amount must be whole cents.")
    if amount_cents <= 0 or amount_cents > get_settings().max_payment_cents:
        raise ApiError(422, "invalid_amount", "That amount isn't allowed.")


# ---------------------------------------------------------------- requests


async def create_request(
    s: AsyncSession, merchant: Merchant, provider: PaymentProvider, *, source: str,
    amount_cents: int, expires_in: timedelta, description: str | None = None,
    created_by: uuid.UUID | None = None, static_qr_id: uuid.UUID | None = None,
    payment_link_id: uuid.UUID | None = None, pos_integration_id: uuid.UUID | None = None,
    external_order_id: str | None = None, idempotency_key: str | None = None,
    body_hash: bytes | None = None,
) -> PaymentRequest:
    check_amount(amount_cents)
    await check_can_accept(s, merchant, provider)
    req = PaymentRequest(
        id=uuid7(), merchant_id=merchant.id, checkout_token=new_checkout_token(), source=source,
        static_qr_id=static_qr_id, payment_link_id=payment_link_id,
        pos_integration_id=pos_integration_id, external_order_id=external_order_id,
        idempotency_key=idempotency_key, request_hash=body_hash, amount_cents=amount_cents,
        description=description, reference=new_reference(),
        expires_at=clock.now() + expires_in, created_by=created_by,
    )
    s.add(req)
    await s.flush()
    await audit.record(s, "payment_request.created", actor_user_id=created_by,
                       merchant_id=merchant.id, target_type="payment_request",
                       target_id=str(req.id),
                       metadata={"source": source, "amount_cents": amount_cents})
    return req


@dataclass
class StartResult:
    attempt_id: uuid.UUID
    authorisation_url: str


async def start_attempt(merchant_id: uuid.UUID, checkout_token: str,
                        provider: PaymentProvider) -> StartResult:
    """Begin (or resume) the bank step for a request. Safe to call repeatedly."""
    settings = get_settings()
    # 1. Under a lock on the request, decide, then record the attempt *before* calling out.
    async with merchant_tx(merchant_id) as s:
        req = (await s.execute(select(PaymentRequest).where(
            PaymentRequest.checkout_token == checkout_token).with_for_update())).scalar_one()
        if req.status != "open":
            raise ApiError(409, f"request_{req.status}", _closed_message(req.status))
        if req.expires_at <= clock.now():
            raise ApiError(409, "request_expired", _closed_message("expired"))
        current = (await s.execute(select(PaymentAttempt).where(
            PaymentAttempt.payment_request_id == req.id,
            PaymentAttempt.status.in_([str(x) for x in IN_FLIGHT])))).scalar_one_or_none()
        if current is not None:
            if current.status == Status.AWAITING_AUTHORISATION and current.authorisation_url:
                return StartResult(current.id, current.authorisation_url)  # resume, don't duplicate
            if current.status == Status.CREATED:
                raise ApiError(409, "payment_starting", "Your payment is starting. Try again.")
            raise ApiError(409, "payment_in_progress",
                           "Your bank is already processing this payment. Please don't pay again.")
        merchant = (await s.execute(select(Merchant).where(Merchant.id == merchant_id))
                    ).scalar_one()
        await check_can_accept(s, merchant, provider)
        account = await verified_account(s, merchant_id)
        assert account is not None
        attempt = PaymentAttempt(
            id=uuid7(), merchant_id=merchant_id, payment_request_id=req.id,
            provider=provider.name, live=provider.live, amount_cents=req.amount_cents,
            reference=req.reference, destination_account_id=account.id,
            destination_name=account.holder_name,
            destination_account_enc=account.account_number_enc,
            destination_last4=account.account_last4, status=Status.CREATED,
        )
        s.add(attempt)
        await s.flush()
        provider_request = CreatePayment(
            amount_cents=req.amount_cents, payee_name=account.holder_name,
            payee_account=decrypt_field(account.account_number_enc), reference=req.reference,
            payer_reference=req.reference,
            redirect_uri=f"{settings.web_origin}/c/{req.checkout_token}/return",
            webhook_uri=(f"{settings.public_api_url}/v1/webhooks/{provider.name}"
                         f"?attempt={attempt.id}"),
            expires_at=max(req.expires_at, clock.now() + PROVIDER_MIN_EXPIRY),
        )
        attempt_id = attempt.id

    # 2. Call the provider with no locks held.
    try:
        created = await provider.create_payment(provider_request)
    except ProviderError as exc:
        # No authorisation link reached the customer, so no money can move: a clean failure.
        async with merchant_tx(merchant_id) as s:
            a = await _lock_attempt(s, attempt_id)
            if a.status == Status.CREATED:
                await _transition(s, a, Status.FAILED, method="provider_poll",
                                  reason=f"create_{exc.code}")
        if exc.kind == ErrorKind.TEMPORARY:
            raise ApiError(503, "provider_unavailable",
                           "We couldn't reach the bank payment service. Please try again.") from exc
        raise ApiError(502, "provider_refused",
                       "The bank payment service couldn't start this payment.") from exc

    # 3. Record the provider's payment.
    async with merchant_tx(merchant_id) as s:
        a = await _lock_attempt(s, attempt_id)
        a.provider_payment_id = created.provider_payment_id
        a.authorisation_url = created.authorisation_url
        a.provider_expires_at = created.expires_at
        a.next_poll_at = clock.now() + timedelta(seconds=POLL_SCHEDULE[0])
        await _transition(s, a, Status.AWAITING_AUTHORISATION, method="provider_poll",
                          provider_status=created.status)
    assert created.authorisation_url
    return StartResult(attempt_id, created.authorisation_url)


def _closed_message(status: str) -> str:
    return {
        "paid": "This has already been paid.",
        "cancelled": "This payment request was cancelled.",
        "expired": "This payment request has expired. Ask for a new one.",
        "requires_review": "This payment is being checked. Please don't pay again.",
    }.get(status, "This payment request is closed.")


async def _lock_attempt(s: AsyncSession, attempt_id: uuid.UUID) -> PaymentAttempt:
    return (await s.execute(select(PaymentAttempt).where(PaymentAttempt.id == attempt_id)
                            .with_for_update())).scalar_one()


async def _lock_request(s: AsyncSession, request_id: uuid.UUID) -> PaymentRequest:
    return (await s.execute(select(PaymentRequest).where(PaymentRequest.id == request_id)
                            .with_for_update())).scalar_one()


# ---------------------------------------------------------------- applying provider truth


async def apply_provider_payment(
    s: AsyncSession, attempt: PaymentAttempt, payment: ProviderPayment,
    provider: PaymentProvider, *, method: str,
) -> Decision:
    """Apply what the provider says about an attempt (attempt already locked)."""
    now = clock.now()
    attempt.last_polled_at = now
    attempt.provider_status = payment.status
    attempt.provider_status_code = payment.status_code
    target = provider.normalise(payment.status, payment.status_code)

    # Defence in depth: the provider must agree with our immutable snapshot.
    if (payment.amount_cents is not None and payment.amount_cents != attempt.amount_cents) or (
        payment.payee_account is not None
        and payment.payee_account != decrypt_field(attempt.destination_account_enc)
    ):
        target = Status.REQUIRES_REVIEW
        reason = "provider_details_mismatch"
    else:
        reason = payment.status_code

    current = Status(attempt.status)
    decision = decide(current, target)
    if decision == Decision.APPLY:
        await _transition(s, attempt, target, method=method, reason=reason,
                          provider_status=payment.status, provider_code=payment.status_code)
    elif decision == Decision.CONFLICT:
        log.error("payment.provider_conflict", attempt_id=str(attempt.id), current=current,
                  reported=target)
        if current not in FINAL:
            await _transition(s, attempt, Status.REQUIRES_REVIEW, method=method,
                              reason=f"provider_conflict:{target}")
        else:
            req = await _lock_request(s, attempt.payment_request_id)
            await _request_needs_review(s, req, f"provider_conflict:{current}->{target}")
    _schedule_next_poll(attempt)
    return decision


def _schedule_next_poll(attempt: PaymentAttempt) -> None:
    if attempt.status in (Status.AWAITING_AUTHORISATION, Status.AUTHORISED, Status.PROCESSING):
        attempt.poll_count += 1
        delay = POLL_SCHEDULE[min(attempt.poll_count, len(POLL_SCHEDULE) - 1)]
        attempt.next_poll_at = clock.now() + timedelta(seconds=delay)
    else:
        attempt.next_poll_at = None


async def _transition(
    s: AsyncSession, attempt: PaymentAttempt, target: Status, *, method: str,
    reason: str | None = None, provider_status: str | None = None,
    provider_code: str | None = None, operator_id: uuid.UUID | None = None,
    note: str | None = None,
) -> None:
    """Move an attempt to ``target`` and update its request. Caller holds the attempt lock."""
    now = clock.now()
    req = await _lock_request(s, attempt.payment_request_id)
    from_status = attempt.status

    if target in SUCCESS and req.paid_attempt_id not in (None, attempt.id):
        # A second successful payment for one request: money moved twice. Never hide it.
        target, reason = Status.REQUIRES_REVIEW, "duplicate_payment"

    attempt.status = target
    attempt.status_reason = reason
    attempt.last_event_at = now
    if target == Status.AUTHORISED or (target in COMMITTED and attempt.authorised_at is None):
        attempt.authorised_at = attempt.authorised_at or now
    if target == Status.SENT:
        attempt.sent_at = now
    if target in FINAL or target == Status.SENT:
        attempt.terminal_at = attempt.terminal_at or now
    if target not in (Status.AWAITING_AUTHORISATION, Status.AUTHORISED, Status.PROCESSING):
        attempt.next_poll_at = None
    await s.flush()

    s.add(Reconciliation(merchant_id=attempt.merchant_id, attempt_id=attempt.id, method=method,
                         from_status=from_status, to_status=target,
                         provider_status=provider_status, provider_code=provider_code,
                         operator_id=operator_id, note=note))
    await audit.record(s, f"payment_attempt.{target.lower()}", actor_user_id=operator_id,
                       merchant_id=attempt.merchant_id, target_type="payment_attempt",
                       target_id=str(attempt.id),
                       metadata={"from": from_status, "reason": reason, "method": method})

    # Request-level consequences.
    if target == Status.SENT:
        if req.status == "open":
            req.status = "paid"
            req.paid_attempt_id = attempt.id
            req.paid_at = now
            await s.flush()
            if req.payment_link_id:
                await s.execute(text("UPDATE payment_links SET paid_count = paid_count + 1"
                                     " WHERE id = :id"), {"id": req.payment_link_id})
            await emit_event(s, req, "payment.succeeded", attempt)
        elif req.status == "requires_review" and req.paid_attempt_id is None:
            # A review case resolved by later provider truth or an audited admin decision.
            req.status = "paid"
            req.status_reason = f"resolved:{method}"
            req.paid_attempt_id = attempt.id
            req.paid_at = now
            await s.flush()
            await emit_event(s, req, "payment.succeeded", attempt)
        else:
            # Paid after the request was cancelled or expired: someone must decide.
            await _request_needs_review(s, req, "paid_after_close", attempt)
    elif target == Status.REQUIRES_REVIEW:
        await _request_needs_review(s, req, reason or "requires_review", attempt)
    elif target in (Status.FAILED, Status.CANCELLED, Status.EXPIRED):
        if req.status == "open" and req.expires_at <= now:
            req.status = "expired"
            await s.flush()
            await emit_event(s, req, "payment.expired", attempt)
        # Otherwise the request stays open: the customer may try again.


async def _request_needs_review(s: AsyncSession, req: PaymentRequest, reason: str,
                                attempt: PaymentAttempt | None = None) -> None:
    if req.status != "requires_review":
        req.status = "requires_review"
        req.status_reason = reason
        await s.flush()
    await emit_event(s, req, "payment.requires_review", attempt)


# ---------------------------------------------------------------- outcome events


def request_public(req: PaymentRequest) -> dict[str, Any]:
    return {
        "id": str(req.id), "reference": req.reference, "status": req.status,
        "status_reason": req.status_reason, "amount_cents": req.amount_cents,
        "currency": req.currency, "description": req.description,
        "external_order_id": req.external_order_id, "source": req.source,
        "checkout_url": f"{get_settings().web_origin}/c/{req.checkout_token}",
        "expires_at": req.expires_at.isoformat(),
        "paid_at": req.paid_at.isoformat() if req.paid_at else None,
        "created_at": req.created_at.isoformat() if req.created_at else None,
    }


async def emit_event(s: AsyncSession, req: PaymentRequest, event_type: str,
                     attempt: PaymentAttempt | None) -> None:
    """Record an outcome once per request and queue it for the merchant's webhooks."""
    event_id = uuid7()
    payload = {
        "id": str(event_id), "type": event_type, "created_at": clock.now().isoformat(),
        "data": {"payment_request": request_public(req),
                 "attempt": None if attempt is None else {
                     "id": str(attempt.id), "status": attempt.status,
                     "reason": attempt.status_reason}},
    }
    inserted = (await s.execute(text(
        "INSERT INTO payment_events (id, merchant_id, payment_request_id, event_type, payload)"
        " VALUES (:id, :m, :r, :t, CAST(:p AS jsonb))"
        " ON CONFLICT (payment_request_id, event_type) DO NOTHING RETURNING id"),
        {"id": event_id, "m": req.merchant_id, "r": req.id, "t": event_type,
         "p": json.dumps(payload)})).first()
    if inserted is None or req.pos_integration_id is None:
        return
    endpoints = (await s.execute(select(WebhookEndpoint).where(
        WebhookEndpoint.pos_integration_id == req.pos_integration_id,
        WebhookEndpoint.enabled.is_(True)))).scalars().all()
    for ep in endpoints:
        s.add(WebhookDelivery(merchant_id=req.merchant_id, endpoint_id=ep.id, event_id=event_id,
                              event_type=event_type, payload=payload))
    await s.flush()


# ---------------------------------------------------------------- refresh, cancel, expiry


async def refresh_attempt(merchant_id: uuid.UUID, attempt_id: uuid.UUID,
                          provider: PaymentProvider, *, method: str) -> None:
    """Ask the provider for the latest status and apply it."""
    async with merchant_tx(merchant_id) as s:
        a = (await s.execute(select(PaymentAttempt).where(PaymentAttempt.id == attempt_id))
             ).scalar_one()
        pid = a.provider_payment_id
    if pid is None:
        return
    try:
        payment = await provider.get_payment(pid)
    except ProviderError as exc:
        log.warning("payment.poll_failed", attempt_id=str(attempt_id), code=exc.code)
        async with merchant_tx(merchant_id) as s:
            a = await _lock_attempt(s, attempt_id)
            a.last_polled_at = clock.now()
            _schedule_next_poll(a)
        return
    async with merchant_tx(merchant_id) as s:
        a = await _lock_attempt(s, attempt_id)
        await apply_provider_payment(s, a, payment, provider, method=method)


async def cancel_request(merchant_id: uuid.UUID, request_id: uuid.UUID,
                         provider: PaymentProvider, actor_user_id: uuid.UUID | None,
                         *, expire: bool = False) -> PaymentRequest:
    """Cancel (or expire) an open request. Refuses if the customer has already approved."""
    final = "expired" if expire else "cancelled"
    async with merchant_tx(merchant_id) as s:
        req = await _lock_request(s, request_id)
        if req.status != "open":
            if req.status == final:
                return req
            raise ApiError(409, f"request_{req.status}", _closed_message(req.status))
        a = (await s.execute(select(PaymentAttempt).where(
            PaymentAttempt.payment_request_id == req.id,
            PaymentAttempt.status.in_([str(x) for x in IN_FLIGHT])))).scalar_one_or_none()
        if a is not None and Status(a.status) in COMMITTED:
            raise ApiError(409, "payment_in_progress",
                           "The customer has approved this payment, so it can't be cancelled.")
        pending = a.id if a is not None and a.status == Status.AWAITING_AUTHORISATION else None
        pid = a.provider_payment_id if a is not None else None
        stuck = a.id if a is not None and a.status == Status.CREATED else None

    if pending and pid:
        try:
            await provider.cancel_payment(pid)
        except ProviderError:
            # Probably approved meanwhile. Re-read the truth instead of cancelling.
            await refresh_attempt(merchant_id, pending, provider, method="expiry")
            raise ApiError(409, "payment_in_progress",
                           "The customer has just approved this payment.") from None
        await refresh_attempt(merchant_id, pending, provider, method="expiry")

    async with merchant_tx(merchant_id) as s:
        if stuck:
            sa = await _lock_attempt(s, stuck)
            if sa.status == Status.CREATED:
                await _transition(s, sa, Status.FAILED, method="expiry", reason="request_closed")
        req = await _lock_request(s, request_id)
        if pending:
            a2 = await _lock_attempt(s, pending)
            if Status(a2.status) in COMMITTED:
                raise ApiError(409, "payment_in_progress",
                               "The customer has just approved this payment.")
            if a2.status == Status.AWAITING_AUTHORISATION:
                await _transition(s, a2, Status.CANCELLED, method="expiry",
                                  reason="request_closed")
        if req.status == "open":
            req.status = final
            await s.flush()
            await audit.record(s, f"payment_request.{final}", actor_user_id=actor_user_id,
                               merchant_id=merchant_id, target_type="payment_request",
                               target_id=str(req.id))
            await emit_event(s, req, f"payment.{final}", None)
        return req


async def link_for_code(s: AsyncSession, code: str) -> PaymentLink | None:
    return (await s.execute(select(PaymentLink).where(PaymentLink.code == code))
            ).scalar_one_or_none()
