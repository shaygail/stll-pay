"""Chooses the configured payment provider."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.payments.providers.akahu import AkahuPaymentProvider
from app.payments.providers.base import PaymentProvider
from app.payments.providers.mock import MockPaymentProvider

_override: PaymentProvider | None = None


def set_provider_override(provider: PaymentProvider | None) -> None:
    """Tests only: use a specific provider instance (for example Akahu with a fake server)."""
    global _override
    _override = provider


def get_provider() -> PaymentProvider:
    return _override or _configured_provider()


@lru_cache
def _configured_provider() -> PaymentProvider:
    settings = get_settings()
    if settings.payments_provider == "akahu":
        return AkahuPaymentProvider(live=settings.live_payments_enabled)
    return MockPaymentProvider()


def get_mock_provider() -> MockPaymentProvider | None:
    provider = get_provider()
    return provider if isinstance(provider, MockPaymentProvider) else None
