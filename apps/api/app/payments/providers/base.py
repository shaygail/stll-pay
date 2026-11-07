"""The payment provider interface. Mock and Akahu implement it; others can later."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from app.payments.states import Status


class ErrorKind(StrEnum):
    # Nothing was created; safe to try again with a new attempt.
    TEMPORARY = "temporary"
    # The provider refused the request (for example invalid destination). Don't retry.
    PERMANENT = "permanent"


class ProviderError(Exception):
    def __init__(self, kind: ErrorKind, code: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.code = code


@dataclass(frozen=True)
class CreatePayment:
    amount_cents: int
    payee_name: str
    payee_account: str
    reference: str  # appears on the merchant's statement; at most 12 characters
    payer_reference: str  # appears on the customer's statement; at most 12 characters
    redirect_uri: str
    webhook_uri: str
    expires_at: datetime


@dataclass(frozen=True)
class ProviderPayment:
    provider_payment_id: str
    status: str  # the provider's own status, verbatim
    status_code: str | None
    authorisation_url: str | None = None
    expires_at: datetime | None = None
    amount_cents: int | None = None
    payee_account: str | None = None


@dataclass(frozen=True)
class WebhookNotice:
    """A verified webhook. Only tells us *which* payment changed; we re-fetch the state."""

    provider_payment_id: str
    status: str | None
    status_code: str | None
    emitted_at: datetime | None


class PaymentProvider(Protocol):
    name: str
    live: bool

    async def create_payment(self, request: CreatePayment) -> ProviderPayment: ...

    async def get_payment(self, provider_payment_id: str) -> ProviderPayment: ...

    async def cancel_payment(self, provider_payment_id: str) -> None: ...

    async def verify_webhook(self, raw_body: bytes, headers: dict[str, str]) -> WebhookNotice: ...

    def normalise(self, status: str, status_code: str | None) -> Status: ...


class WebhookRejected(Exception):
    """Signature or format check failed. The webhook must be discarded."""
