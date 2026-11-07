"""Chooses the configured payment provider."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.payments.providers.akahu import AkahuPaymentProvider
from app.payments.providers.base import PaymentProvider
from app.payments.providers.mock import MockPaymentProvider


@lru_cache
def get_provider() -> PaymentProvider:
    settings = get_settings()
    if settings.payments_provider == "akahu":
        return AkahuPaymentProvider(live=settings.live_payments_enabled)
    return MockPaymentProvider()


def get_mock_provider() -> MockPaymentProvider | None:
    provider = get_provider()
    return provider if isinstance(provider, MockPaymentProvider) else None
