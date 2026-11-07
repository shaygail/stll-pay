"""Background worker: polling, expiry, stuck-attempt recovery and webhook delivery.

Run with ``uv run python -m app.worker``. Everything it does is driven by rows in the
database, so it can be stopped and restarted at any time, and running two copies is safe
(rows are claimed with ``FOR UPDATE SKIP LOCKED``).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import signal
import time
import uuid
from datetime import timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import select, text

from app.core import audit, clock
from app.core.logging import configure_logging
from app.core.security import decrypt_field
from app.payments import service
from app.payments.models import PaymentAttempt, PaymentRequest, WebhookDelivery, WebhookEndpoint
from app.payments.providers.base import PaymentProvider
from app.payments.providers.registry import get_mock_provider, get_provider
from app.payments.states import Status

log = structlog.get_logger()

DELIVERY_BACKOFF = [10, 30, 60, 300, 900, 1800, 3600, 7200, 14400, 28800, 43200, 86400]
SIGNATURE_HEADER = "STLL-Signature"


def sign_payload(secret: str, body: bytes, timestamp: int) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={mac.hexdigest()}"


async def poll_due_attempts(provider: PaymentProvider, limit: int = 50) -> int:
    """Ask the provider about attempts whose next check is due."""
    async with service.system_tx() as s:
        rows = (await s.execute(text(
            "SELECT id, merchant_id FROM payment_attempts WHERE next_poll_at <= :now"
            " AND status IN ('AWAITING_AUTHORISATION', 'AUTHORISED', 'PROCESSING')"
            " ORDER BY next_poll_at LIMIT :limit FOR UPDATE SKIP LOCKED"),
            {"now": clock.now(), "limit": limit})).all()
        # Push them back briefly so another worker doesn't pick them at the same time.
        for attempt_id, _ in rows:
            await s.execute(text("UPDATE payment_attempts SET next_poll_at = :t WHERE id = :id"),
                            {"t": clock.now() + timedelta(seconds=30), "id": attempt_id})
    for attempt_id, merchant_id in rows:
        await service.refresh_attempt(merchant_id, attempt_id, provider, method="provider_poll")
    return len(rows)


async def recover_stuck_attempts() -> int:
    """Attempts left in CREATED (crash before the provider answered) never gave the customer
    an authorisation link, so no money can move: fail them. Attempts approved but with no
    final result after 24 hours need a human."""
    count = 0
    async with service.system_tx() as s:
        stuck = (await s.execute(select(PaymentAttempt).where(
            PaymentAttempt.status == Status.CREATED,
            PaymentAttempt.created_at <= clock.now() - service.STUCK_CREATED_AFTER,
        ).with_for_update(skip_locked=True).limit(100))).scalars().all()
        for a in stuck:
            await service._transition(s, a, Status.FAILED, method="provider_poll",
                                      reason="creation_interrupted")
            count += 1
        silent = (await s.execute(select(PaymentAttempt).where(
            PaymentAttempt.status.in_([Status.AUTHORISED, Status.PROCESSING]),
            PaymentAttempt.authorised_at <= clock.now() - service.NO_RESULT_REVIEW_AFTER,
        ).with_for_update(skip_locked=True).limit(100))).scalars().all()
        for a in silent:
            await service._transition(s, a, Status.REQUIRES_REVIEW, method="provider_poll",
                                      reason="no_final_status")
            count += 1
    return count


async def expire_due_requests(provider: PaymentProvider, limit: int = 50) -> int:
    async with service.system_tx() as s:
        rows = (await s.execute(select(PaymentRequest.id, PaymentRequest.merchant_id).where(
            PaymentRequest.status == "open", PaymentRequest.expires_at <= clock.now(),
        ).limit(limit))).all()
    expired = 0
    for request_id, merchant_id in rows:
        try:
            await service.cancel_request(merchant_id, request_id, provider, None, expire=True)
            expired += 1
        except Exception as exc:  # approved meanwhile: it stays open until the bank answers
            log.info("payment.expiry_skipped", request_id=str(request_id), reason=str(exc))
    return expired


async def deliver_webhooks(client: httpx.AsyncClient, limit: int = 50) -> int:
    """Send queued events to merchants' webhook endpoints (signed, retried with backoff)."""
    async with service.system_tx() as s:
        due = (await s.execute(select(WebhookDelivery, WebhookEndpoint).join(
            WebhookEndpoint, WebhookEndpoint.id == WebhookDelivery.endpoint_id).where(
            WebhookDelivery.status == "pending", WebhookDelivery.next_attempt_at <= clock.now(),
        ).order_by(WebhookDelivery.created_at).limit(limit).with_for_update(
            of=WebhookDelivery, skip_locked=True))).all()
        jobs: list[tuple[uuid.UUID, str, str, dict[str, Any], str]] = []
        for d, ep in due:
            d.next_attempt_at = clock.now() + timedelta(seconds=60)  # claim
            jobs.append((d.id, ep.url, decrypt_field(ep.secret_enc), d.payload, str(d.event_id)))

    for delivery_id, url, secret, payload, event_id in jobs:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        headers = {"content-type": "application/json", "STLL-Event-Id": event_id,
                   SIGNATURE_HEADER: sign_payload(secret, body, int(time.time()))}
        code: int | None = None
        error: str | None = None
        try:
            r = await client.post(url, content=body, headers=headers, timeout=10)
            code = r.status_code
        except httpx.HTTPError as exc:
            error = type(exc).__name__
        async with service.system_tx() as s:
            d = (await s.execute(select(WebhookDelivery).where(WebhookDelivery.id == delivery_id)
                                 .with_for_update())).scalar_one()
            d.attempts += 1
            d.last_status_code = code
            d.last_error = error
            if code is not None and 200 <= code < 300:
                d.status = "delivered"
                d.delivered_at = clock.now()
            elif d.attempts >= len(DELIVERY_BACKOFF):
                d.status = "failed"
                await audit.record(s, "webhook.delivery_failed", merchant_id=d.merchant_id,
                                   target_type="webhook_delivery", target_id=str(d.id))
            else:
                d.next_attempt_at = clock.now() + timedelta(
                    seconds=DELIVERY_BACKOFF[d.attempts - 1])
    return len(jobs)


async def deliver_mock_webhooks(client: httpx.AsyncClient) -> int:
    """The mock provider's own outbound webhooks (stands in for Akahu sending them)."""
    mock = get_mock_provider()
    if mock is None:
        return 0
    await mock.advance()
    sent = 0
    for w in await mock.due_webhooks():
        try:
            r = await client.post(w["target"], content=w["body"], timeout=10, headers={
                "content-type": "application/json",
                "X-Akahu-Payments-Signature": w["signature"],
                "X-Akahu-Payments-Signing-Key": w["key_id"]})
            ok = 200 <= r.status_code < 300
        except httpx.HTTPError:
            ok = False
        await mock.mark_webhook(w["id"], ok)
        sent += 1
    return sent


async def run_once(client: httpx.AsyncClient, provider: PaymentProvider | None = None) -> dict[
        str, int]:
    provider = provider or get_provider()
    return {
        "mock_webhooks": await deliver_mock_webhooks(client),
        "polled": await poll_due_attempts(provider),
        "recovered": await recover_stuck_attempts(),
        "expired": await expire_due_requests(provider),
        "webhooks": await deliver_webhooks(client),
    }


async def main(interval: float = 2.0) -> None:
    configure_logging()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    log.info("worker.start")
    async with httpx.AsyncClient() as client:
        while not stop.is_set():
            try:
                counts = await run_once(client)
                if any(counts.values()):
                    log.info("worker.tick", **counts)
            except Exception:
                log.exception("worker.tick_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass
    log.info("worker.stop")


if __name__ == "__main__":
    asyncio.run(main())
