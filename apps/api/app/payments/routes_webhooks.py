"""Provider webhook intake.

Order of operations, per Akahu's guidance (docs/AKAHU_INTEGRATION.md §4):
1. Verify the RSA signature on the raw body. Reject anything that fails.
2. Deduplicate: a replayed webhook is acknowledged and ignored.
3. Treat the webhook only as a hint that something changed: fetch the payment from the
   provider with a GET and apply *that*. Out-of-order or forged-but-valid-looking events
   can't move a payment to the wrong state.
4. Answer quickly with 2xx (Akahu allows 10 seconds and retries up to 20 times).
"""

from __future__ import annotations

import hashlib
import uuid

import structlog
from fastapi import APIRouter, Request
from sqlalchemy import select, text

from app.core import clock
from app.core.errors import ApiError, not_found
from app.core.ratelimit import limiter
from app.payments import service
from app.payments.models import PaymentAttempt
from app.payments.providers.base import ProviderError, WebhookRejected
from app.payments.providers.registry import get_provider

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])
log = structlog.get_logger()

MAX_BODY = 65_536


@router.post("/{provider_name}")
async def receive(provider_name: str, request: Request) -> dict[str, object]:
    provider = get_provider()
    if provider_name != provider.name:
        raise not_found()
    limiter.check(f"webhook:{request.client.host if request.client else 'x'}", 600, 60)
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise ApiError(413, "too_large", "Body too large.")
    headers = {k.lower(): v for k, v in request.headers.items()}

    try:
        notice = await provider.verify_webhook(raw, headers)
    except WebhookRejected as exc:
        log.warning("webhook.rejected", provider=provider.name, reason=str(exc))
        raise ApiError(401, "invalid_signature", "Webhook signature check failed.") from exc

    dedup = hashlib.sha256("|".join([
        provider.name, notice.provider_payment_id, notice.status or "", notice.status_code or "",
        notice.emitted_at.isoformat() if notice.emitted_at else "", hashlib.sha256(raw).hexdigest(),
    ]).encode()).digest()

    async with service.system_tx() as s:
        merchant_id = (await s.execute(text("SELECT lookup_attempt_by_provider_id(:p, :i)"),
                                       {"p": provider.name, "i": notice.provider_payment_id})
                       ).scalar()
        attempt_id = None
        if merchant_id is not None:
            attempt_id = (await s.execute(select(PaymentAttempt.id).where(
                PaymentAttempt.provider == provider.name,
                PaymentAttempt.provider_payment_id == notice.provider_payment_id))).scalar()
        hint = request.query_params.get("attempt")
        if attempt_id is not None and hint and hint != str(attempt_id):
            log.warning("webhook.attempt_mismatch", attempt=str(attempt_id), hint=hint)
        event_id = uuid.uuid4()
        inserted = (await s.execute(text(
            "INSERT INTO payment_provider_events (id, provider, merchant_id, attempt_id,"
            " provider_payment_id, dedup_key, signature_valid, status, status_code, emitted_at,"
            " raw_body) VALUES (:id, :p, :m, :a, :pid, :k, true, :st, :code, :em, :raw)"
            " ON CONFLICT (dedup_key) DO NOTHING RETURNING id"),
            {"id": event_id, "p": provider.name, "m": merchant_id, "a": attempt_id,
             "pid": notice.provider_payment_id, "k": dedup, "st": notice.status,
             "code": notice.status_code, "em": notice.emitted_at,
             "raw": raw.decode("utf-8", "replace")})).first()
    if inserted is None:
        return {"ok": True, "duplicate": True}
    if merchant_id is None or attempt_id is None:
        await _finish(event_id, "unknown_payment")
        return {"ok": True, "outcome": "unknown_payment"}

    try:
        payment = await provider.get_payment(notice.provider_payment_id)
    except ProviderError:
        # The poller will pick it up; acknowledge so the provider doesn't hammer us.
        await _finish(event_id, "confirm_failed")
        return {"ok": True, "outcome": "confirm_failed"}
    async with service.merchant_tx(merchant_id) as s:
        a = await service._lock_attempt(s, attempt_id)
        decision = await service.apply_provider_payment(s, a, payment, provider, method="webhook")
    await _finish(event_id, str(decision))
    return {"ok": True, "outcome": str(decision)}


async def _finish(event_id: uuid.UUID, outcome: str) -> None:
    async with service.system_tx() as s:
        await s.execute(text("UPDATE payment_provider_events SET processed_at = :now,"
                             " outcome = :o WHERE id = :id"),
                        {"now": clock.now(), "o": outcome, "id": event_id})
