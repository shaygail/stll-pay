"""AkahuPaymentProvider: Akahu one-off payments over regulated open banking.

Endpoints, fields and statuses are from Akahu's documentation (docs/AKAHU_INTEGRATION.md):

| Call | Endpoint |
|---|---|
| Create | POST /v1/one-off-payments -> {_id, authorisation_url, expires_at} |
| Get | GET /v1/one-off-payments/{id} -> the payment |
| Cancel | PUT /v1/one-off-payments/{id}/cancel -> 204 (only while READY) |
| Signing key | GET /v1/webhooks/keys/{keyId} -> {keyId, publicKey} |
| Banks and limits | GET /v1/connections |

Authentication is HTTP Basic: App Token as username, App Secret as password. Credentials
come only from server-side settings and are never logged.

Real money moves only when the credentials are production ones *and* every live switch is
on (see app/core/config.py). Demo Bank is used with sandbox credentials.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from cryptography.hazmat.primitives import serialization

from app.core.config import get_settings
from app.payments.providers.akahu_status import normalise_akahu
from app.payments.providers.akahu_wire import (
    KEY_HEADER,
    SIGNATURE_HEADER,
    cents_to_dollars,
    dollars_to_cents,
    parse_notice,
    parse_time,
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

log = structlog.get_logger()

# Shown on the merchant's statement next to our reference (max 12 characters).
PAYEE_PARTICULARS = "STLL PAY"


class AkahuPaymentProvider:
    name = "akahu"

    def __init__(self, live: bool, transport: httpx.AsyncBaseTransport | None = None) -> None:
        settings = get_settings()
        if settings.akahu_app_token is None or settings.akahu_app_secret is None:
            raise RuntimeError("Akahu credentials are not configured")
        self.live = live
        self._client = httpx.AsyncClient(
            base_url=settings.akahu_api_url,
            auth=httpx.BasicAuth(settings.akahu_app_token.get_secret_value(),
                                 settings.akahu_app_secret.get_secret_value()),
            timeout=httpx.Timeout(10.0, connect=5.0),
            headers={"accept": "application/json", "user-agent": "stll-pay/0.1"},
            transport=transport,
        )
        self._keys: dict[str, Any] = {}

    # ------------------------------------------------------------ HTTP

    async def _call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            response = await self._client.request(method, path, **kw)
        except httpx.TimeoutException as exc:
            raise ProviderError(ErrorKind.TEMPORARY, "provider_timeout",
                                "Akahu did not answer in time") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorKind.TEMPORARY, "provider_unreachable",
                                "Could not reach Akahu") from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise ProviderError(ErrorKind.TEMPORARY, "provider_error",
                                f"Akahu returned {response.status_code}")
        if response.status_code in (401, 403):
            log.error("akahu.auth_failed", status=response.status_code, path=path)
            raise ProviderError(ErrorKind.PERMANENT, "provider_auth",
                                "Akahu refused our credentials")
        return response

    @staticmethod
    def _error_code(response: httpx.Response) -> str:
        try:
            issues = response.json().get("issues") or []
            return str(issues[0].get("code")) if issues else "bad_request"
        except (ValueError, AttributeError, IndexError):
            return "bad_request"

    # ------------------------------------------------------------ provider API

    async def create_payment(self, request: CreatePayment) -> ProviderPayment:
        body: dict[str, Any] = {
            "amount": cents_to_dollars(request.amount_cents),
            "payee": {
                "name": request.payee_name,
                "account_number": request.payee_account,
                "particulars": PAYEE_PARTICULARS,
                "reference": request.reference,
            },
            "payer": {"reference": request.payer_reference},
            "redirect_uri": request.redirect_uri,
            "expires_at": request.expires_at.isoformat(),
        }
        if request.webhook_uri:
            body["webhook_uri"] = request.webhook_uri
        if request.payer_bank:
            body["payer"]["bank"] = request.payer_bank
        r = await self._call("POST", "/v1/one-off-payments", json=body)
        if r.status_code != 200:
            raise ProviderError(ErrorKind.PERMANENT, self._error_code(r),
                                f"Akahu rejected the payment ({r.status_code})")
        data = r.json()
        return ProviderPayment(
            provider_payment_id=str(data["_id"]), status="READY", status_code=None,
            authorisation_url=str(data["authorisation_url"]),
            expires_at=parse_time(data["expires_at"]) if data.get("expires_at") else None,
            amount_cents=request.amount_cents, payee_account=request.payee_account,
        )

    async def get_payment(self, provider_payment_id: str) -> ProviderPayment:
        r = await self._call("GET", f"/v1/one-off-payments/{provider_payment_id}")
        if r.status_code == 404:
            raise ProviderError(ErrorKind.PERMANENT, "not_found", "Akahu has no such payment")
        if r.status_code != 200:
            raise ProviderError(ErrorKind.TEMPORARY, self._error_code(r),
                                f"Akahu returned {r.status_code}")
        data = r.json()
        payment = data.get("item", data)  # tolerate an {"item": ...} envelope
        reason = payment.get("status_reason") or {}
        payee = payment.get("payee") or {}
        return ProviderPayment(
            provider_payment_id=str(payment["_id"]),
            status=str(payment["status"]),
            status_code=reason.get("code"),
            expires_at=parse_time(payment["expires_at"]) if payment.get("expires_at") else None,
            amount_cents=dollars_to_cents(payment["amount"]) if "amount" in payment else None,
            payee_account=payee.get("account_number"),
        )

    async def cancel_payment(self, provider_payment_id: str) -> None:
        r = await self._call("PUT", f"/v1/one-off-payments/{provider_payment_id}/cancel")
        if r.status_code != 204:
            raise ProviderError(ErrorKind.PERMANENT, "not_cancellable",
                                f"Akahu refused to cancel ({self._error_code(r)})")

    async def verify_webhook(self, raw_body: bytes, headers: dict[str, str]) -> WebhookNotice:
        key_id = headers.get(KEY_HEADER)
        signature = headers.get(SIGNATURE_HEADER)
        if not key_id or not signature or len(key_id) > 200:
            raise WebhookRejected("missing signature headers")
        verify_signature(await self._public_key(key_id), raw_body, signature)
        return parse_notice(raw_body)

    def normalise(self, status: str, status_code: str | None) -> Status:
        return normalise_akahu(status, status_code)

    async def connections(self) -> list[dict[str, Any]]:
        """Banks available for one-off payments and their limits."""
        r = await self._call("GET", "/v1/connections")
        if r.status_code != 200:
            raise ProviderError(ErrorKind.TEMPORARY, self._error_code(r),
                                f"Akahu returned {r.status_code}")
        return list(r.json().get("items", []))

    async def _public_key(self, key_id: str) -> Any:
        """Fetch and cache Akahu's webhook signing key (as Akahu recommends)."""
        if key_id not in self._keys:
            try:
                r = await self._call("GET", f"/v1/webhooks/keys/{key_id}")
            except ProviderError as exc:
                raise WebhookRejected("could not fetch signing key") from exc
            if r.status_code != 200:
                raise WebhookRejected("unknown signing key")
            pem = r.json().get("publicKey", "")
            try:
                self._keys[key_id] = serialization.load_pem_public_key(pem.encode())
            except ValueError as exc:
                raise WebhookRejected("invalid signing key") from exc
        return self._keys[key_id]
