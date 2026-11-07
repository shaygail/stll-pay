"""MockPaymentProvider: a stand-in for Akahu that moves no money.

It behaves like an external provider: it keeps its own state (schema ``mock_provider``),
changes payments on its own schedule, and sends RSA-signed webhooks in Akahu's one-off
payment format. Outcomes follow Akahu Demo Bank's documented magic amounts
(https://developers.akahu.nz/docs/demo-bank), so the same scenarios can be re-run against
Demo Bank in Phase 5. Mock-only extras (no Akahu equivalent) use 5.05-5.09.

| Amount | Outcome |
|---|---|
| 5.01 | create fails with a server error |
| 5.02 | FAILED / INTERNAL_ERROR after approval |
| 5.03 | FAILED / BANK_ERROR |
| 5.04 | FAILED / BANK_ERROR_INDETERMINATE |
| 5.15 | FAILED / REJECTED |
| 5.20 | SENT after 30 s |
| 5.22 | FAILED / REJECTED after 30 s |
| 5.23 | SENT after 180 s |
| 5.24 | FAILED / REJECTED after 180 s |
| 5.05 | mock: create times out (payment created, no response) |
| 5.06 | mock: no webhooks at all (polling must recover) |
| 5.07 | mock: every webhook delivered twice (replays) |
| 5.08 | mock: final webhook delivered before the earlier ones (out of order) |
| other | SENT straight after approval |

A leading 105. behaves like 5. (as in Demo Bank), e.g. 105.20 is 5.20.
"""

from __future__ import annotations

import base64
import json
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy import text

from app.core import clock
from app.core.db import get_sessionmaker
from app.payments.providers.akahu_status import normalise_akahu
from app.payments.providers.akahu_wire import (
    KEY_HEADER,
    SIGNATURE_HEADER,
    parse_notice,
    verify_signature,
)
from app.payments.providers.base import (
    CreatePayment,
    ErrorKind,
    ProviderError,
    ProviderPayment,
    WebhookNotice,
    WebhookRejected,
)
from app.payments.states import Status


def scenario_cents(amount_cents: int) -> int:
    return amount_cents - 10_000 if 10_500 <= amount_cents < 10_600 else amount_cents


# amount -> (final status, final code, delay seconds)
_FINAL: dict[int, tuple[str, str | None, int]] = {
    502: ("FAILED", "INTERNAL_ERROR", 0),
    503: ("FAILED", "BANK_ERROR", 0),
    504: ("FAILED", "BANK_ERROR_INDETERMINATE", 0),
    515: ("FAILED", "REJECTED", 0),
    520: ("SENT", None, 30),
    522: ("FAILED", "REJECTED", 30),
    523: ("SENT", None, 180),
    524: ("FAILED", "REJECTED", 180),
}


def _path_only(url: str) -> str:
    """The mock bank lives on the same site, so send the browser back with a relative URL."""
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


class MockPaymentProvider:
    name = "mock"
    live = False

    def __init__(self) -> None:
        self._public_keys: dict[str, Any] = {}

    # ------------------------------------------------------------ provider API

    async def create_payment(self, request: CreatePayment) -> ProviderPayment:
        c = scenario_cents(request.amount_cents)
        if c == 501:
            raise ProviderError(ErrorKind.TEMPORARY, "provider_error", "Mock provider returned 500")
        if len(request.reference) > 12 or len(request.payer_reference) > 12:
            raise ProviderError(ErrorKind.PERMANENT, "invalid_reference", "Reference too long")
        if request.expires_at < clock.now() + timedelta(minutes=15):
            raise ProviderError(ErrorKind.PERMANENT, "invalid_expiry",
                                "expires_at must be at least 15 minutes ahead")
        pid = "mock_payment_" + secrets.token_hex(12)
        async with get_sessionmaker()() as s, s.begin():
            await s.execute(text(
                "INSERT INTO mock_provider.payments (id, amount_cents, payee_name, payee_account,"
                " payee_reference, redirect_uri, webhook_uri, status, expires_at,"
                " suppress_webhooks, created_at, updated_at) VALUES (:id, :amt, :name, :acct,"
                " :ref, :redir, :hook, 'READY', :exp, :suppress, :now, :now)"),
                {"id": pid, "amt": request.amount_cents, "name": request.payee_name,
                 "acct": request.payee_account, "ref": request.reference,
                 "redir": request.redirect_uri, "hook": request.webhook_uri,
                 "exp": request.expires_at, "suppress": c == 506, "now": clock.now()})
        if c == 505:
            raise ProviderError(ErrorKind.TEMPORARY, "provider_timeout", "Mock provider timed out")
        return ProviderPayment(
            provider_payment_id=pid, status="READY", status_code=None,
            authorisation_url=f"/mock-bank/{pid}",  # relative: the mock bank is part of the web app
            expires_at=request.expires_at, amount_cents=request.amount_cents,
            payee_account=request.payee_account)

    async def get_payment(self, provider_payment_id: str) -> ProviderPayment:
        await self.advance(provider_payment_id)
        row = await self._row(provider_payment_id)
        if row is None:
            raise ProviderError(ErrorKind.PERMANENT, "not_found", "Unknown mock payment")
        return ProviderPayment(
            provider_payment_id=row["id"], status=row["status"], status_code=row["status_code"],
            authorisation_url=f"/mock-bank/{row['id']}",
            expires_at=row["expires_at"], amount_cents=row["amount_cents"],
            payee_account=row["payee_account"])

    async def cancel_payment(self, provider_payment_id: str) -> None:
        await self.advance(provider_payment_id)
        row = await self._row(provider_payment_id)
        if row is None or row["status"] != "READY":
            raise ProviderError(ErrorKind.PERMANENT, "not_cancellable",
                                "Only payments awaiting authorisation can be cancelled")
        await self._set(provider_payment_id, "CANCELLED", "APP_CANCELLED")

    def normalise(self, status: str, status_code: str | None) -> Status:
        return normalise_akahu(status, status_code)

    async def verify_webhook(self, raw_body: bytes, headers: dict[str, str]) -> WebhookNotice:
        key_id = headers.get(KEY_HEADER)
        signature = headers.get(SIGNATURE_HEADER)
        if not key_id or not signature:
            raise WebhookRejected("missing signature headers")
        public_key = await self._public_key(key_id)
        if public_key is None:
            raise WebhookRejected("unknown signing key")
        verify_signature(public_key, raw_body, signature)
        return parse_notice(raw_body)

    # ------------------------------------------------------------ the mock bank's own actions

    async def bank_view(self, provider_payment_id: str) -> dict[str, Any] | None:
        await self.advance(provider_payment_id)
        return await self._row(provider_payment_id)

    async def customer_approves(self, provider_payment_id: str) -> str:
        await self.advance(provider_payment_id)
        row = await self._row(provider_payment_id)
        if row is None or row["status"] != "READY":
            raise ProviderError(ErrorKind.PERMANENT, "not_ready", "This payment can't be approved")
        c = scenario_cents(row["amount_cents"])
        await self._set(provider_payment_id, "AUTHORISED", None, authorised=True)
        await self._set(provider_payment_id, "SUBMITTED", "PENDING")
        status, code, delay = _FINAL.get(c, ("SENT", None, 0))
        if delay:
            await self._schedule(provider_payment_id, status, code, delay)
            await self._set(provider_payment_id, "SUBMITTED", "SETTLEMENT_IN_PROGRESS")
        else:
            await self._set(provider_payment_id, status, code)
        if c == 508:
            await self._reorder_webhooks(provider_payment_id)
        return _path_only(str(row["redirect_uri"]))

    async def customer_cancels(self, provider_payment_id: str) -> str:
        row = await self._row(provider_payment_id)
        if row is None or row["status"] != "READY":
            raise ProviderError(ErrorKind.PERMANENT, "not_ready", "This payment can't be cancelled")
        await self._set(provider_payment_id, "CANCELLED", "USER_CANCELLED")
        return _path_only(str(row["redirect_uri"]))

    async def advance(self, provider_payment_id: str | None = None) -> None:
        """Apply anything due by now: delayed outcomes and expiry of unapproved payments."""
        now = clock.now()
        async with get_sessionmaker()() as s, s.begin():
            due = (await s.execute(text(
                "SELECT id, status, settle_at, final_status, final_code, expires_at"
                " FROM mock_provider.payments WHERE ((settle_at IS NOT NULL AND settle_at <= :now)"
                " OR (status = 'READY' AND expires_at <= :now))"
                " AND (CAST(:id AS text) IS NULL OR id = CAST(:id AS text)) FOR UPDATE"),
                {"now": now, "id": provider_payment_id})).mappings().all()
        for row in due:
            if row["status"] == "READY":
                await self._set(row["id"], "CANCELLED", "TIMED_OUT")
            elif row["final_status"]:
                await self._set(row["id"], row["final_status"], row["final_code"], settle=True)

    async def due_webhooks(self, limit: int = 50) -> list[dict[str, Any]]:
        async with get_sessionmaker()() as s:
            rows = await s.execute(text(
                "SELECT id, target, body, signature, key_id, attempts FROM mock_provider.webhooks"
                " WHERE delivered_at IS NULL AND next_at <= :now AND attempts < 20"
                " ORDER BY id LIMIT :limit"), {"now": clock.now(), "limit": limit})
            return [dict(r) for r in rows.mappings()]

    async def mark_webhook(self, webhook_id: int, delivered: bool) -> None:
        async with get_sessionmaker()() as s, s.begin():
            if delivered:
                await s.execute(text("UPDATE mock_provider.webhooks SET delivered_at = :now,"
                                     " attempts = attempts + 1 WHERE id = :id"),
                                {"now": clock.now(), "id": webhook_id})
            else:
                await s.execute(text(
                    "UPDATE mock_provider.webhooks SET attempts = attempts + 1,"
                    " next_at = :now + make_interval(secs => power(2, attempts + 1))"
                    " WHERE id = :id"), {"now": clock.now(), "id": webhook_id})

    # ------------------------------------------------------------ internals

    async def _row(self, pid: str) -> dict[str, Any] | None:
        async with get_sessionmaker()() as s:
            r = (await s.execute(text("SELECT * FROM mock_provider.payments WHERE id = :id"),
                                 {"id": pid})).mappings().first()
            return dict(r) if r else None

    async def _schedule(self, pid: str, status: str, code: str | None, delay: int) -> None:
        async with get_sessionmaker()() as s, s.begin():
            await s.execute(text(
                "UPDATE mock_provider.payments SET settle_at = :at, final_status = :st,"
                " final_code = :code WHERE id = :id"),
                {"at": clock.now() + timedelta(seconds=delay), "st": status, "code": code,
                 "id": pid})

    async def _set(self, pid: str, status: str, code: str | None, *, authorised: bool = False,
                   settle: bool = False) -> None:
        now = clock.now()
        async with get_sessionmaker()() as s, s.begin():
            row = (await s.execute(text(
                "UPDATE mock_provider.payments SET status = :st, status_code = :code,"
                " updated_at = :now, authorised_at = CASE WHEN :auth THEN :now"
                " ELSE authorised_at END, settle_at = CASE WHEN :settle THEN NULL"
                " ELSE settle_at END WHERE id = :id"
                " RETURNING webhook_uri, suppress_webhooks, amount_cents"),
                {"st": status, "code": code, "now": now, "auth": authorised, "settle": settle,
                 "id": pid})).mappings().one()
            if row["suppress_webhooks"] or not row["webhook_uri"]:
                return
            body: dict[str, Any] = {"type": "ONE_OFF_PAYMENT_STATUS_UPDATE", "_payment": pid,
                                    "status": status, "emitted_at": now.isoformat()}
            if code:
                body["status_reason"] = {"code": code, "message": f"Mock {code}"}
            raw = json.dumps(body, separators=(",", ":"))
            key_id, private_key = await self._signing_key(s)
            signature = base64.b64encode(private_key.sign(raw.encode(), padding.PKCS1v15(),
                                                          hashes.SHA256())).decode()
            copies = 2 if scenario_cents(row["amount_cents"]) == 507 else 1
            for _ in range(copies):
                await s.execute(text(
                    "INSERT INTO mock_provider.webhooks (payment_id, target, body, signature,"
                    " key_id, next_at) VALUES (:pid, :target, :body, :sig, :kid, :now)"),
                    {"pid": pid, "target": row["webhook_uri"], "body": raw, "sig": signature,
                     "kid": key_id, "now": now})

    async def _reorder_webhooks(self, pid: str) -> None:
        """Deliver the newest webhook first, then the older ones."""
        async with get_sessionmaker()() as s, s.begin():
            await s.execute(text(
                "UPDATE mock_provider.webhooks w SET next_at = w.next_at + make_interval("
                "secs => 1 + (SELECT max(id) FROM mock_provider.webhooks WHERE payment_id = :pid)"
                " - w.id) WHERE payment_id = :pid AND id <> (SELECT max(id) FROM"
                " mock_provider.webhooks WHERE payment_id = :pid)"), {"pid": pid})

    async def _signing_key(self, s: Any) -> tuple[str, rsa.RSAPrivateKey]:
        row = (await s.execute(text("SELECT id, private_pem FROM mock_provider.keys"
                                    " ORDER BY created_at LIMIT 1"))).first()
        if row is None:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            kid = "mock_key_" + secrets.token_hex(6)
            private_pem = key.private_bytes(serialization.Encoding.PEM,
                                            serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()).decode()
            public_pem = key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo).decode()
            await s.execute(text("INSERT INTO mock_provider.keys (id, private_pem, public_pem)"
                                 " VALUES (:id, :priv, :pub) ON CONFLICT DO NOTHING"),
                            {"id": kid, "priv": private_pem, "pub": public_pem})
            row = (await s.execute(text("SELECT id, private_pem FROM mock_provider.keys"
                                        " ORDER BY created_at LIMIT 1"))).first()
        loaded = serialization.load_pem_private_key(row[1].encode(), password=None)
        assert isinstance(loaded, rsa.RSAPrivateKey)
        return row[0], loaded

    async def _public_key(self, key_id: str) -> Any:
        # Akahu: fetch GET /v1/webhooks/keys/{keyId} and cache it. Here: read and cache.
        if key_id not in self._public_keys:
            async with get_sessionmaker()() as s:
                pem = (await s.execute(text("SELECT public_pem FROM mock_provider.keys"
                                            " WHERE id = :id"), {"id": key_id})).scalar()
            if pem is None:
                return None
            self._public_keys[key_id] = serialization.load_pem_public_key(pem.encode())
        return self._public_keys[key_id]
