"""AkahuPaymentProvider: placeholder until Phase 5.

The endpoints, request fields and status vocabulary are documented in
docs/AKAHU_INTEGRATION.md. The status mapping already lives in akahu_status.py and is
tested. This class refuses to run until it is implemented and tested against Demo Bank.
"""

from __future__ import annotations

from app.payments.providers.akahu_status import normalise_akahu
from app.payments.providers.base import (
    CreatePayment,
    ErrorKind,
    ProviderError,
    ProviderPayment,
    WebhookNotice,
    WebhookRejected,
)
from app.payments.states import Status

_NOT_READY = "The Akahu provider is not implemented yet (Phase 5)."


class AkahuPaymentProvider:
    name = "akahu"

    def __init__(self, live: bool) -> None:
        self.live = live

    async def create_payment(self, request: CreatePayment) -> ProviderPayment:
        raise ProviderError(ErrorKind.PERMANENT, "provider_not_ready", _NOT_READY)

    async def get_payment(self, provider_payment_id: str) -> ProviderPayment:
        raise ProviderError(ErrorKind.TEMPORARY, "provider_not_ready", _NOT_READY)

    async def cancel_payment(self, provider_payment_id: str) -> None:
        raise ProviderError(ErrorKind.PERMANENT, "provider_not_ready", _NOT_READY)

    async def verify_webhook(self, raw_body: bytes, headers: dict[str, str]) -> WebhookNotice:
        raise WebhookRejected(_NOT_READY)

    def normalise(self, status: str, status_code: str | None) -> Status:
        return normalise_akahu(status, status_code)
