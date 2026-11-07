"""ORM models for payments. Schema source of truth: migrations/versions/0002_payments.py."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, LargeBinary, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, new_id

TS = DateTime(timezone=True)


class BankAccount(Base):
    __tablename__ = "merchant_bank_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID)
    account_number_enc: Mapped[str] = mapped_column(Text)
    account_last4: Mapped[str] = mapped_column(Text)
    holder_name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    verified_by: Mapped[uuid.UUID | None] = mapped_column(UUID)
    verified_at: Mapped[datetime | None] = mapped_column(TS)
    retired_at: Mapped[datetime | None] = mapped_column(TS)


class PosIntegration(Base):
    __tablename__ = "pos_integrations"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID)
    name: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID)
    pos_integration_id: Mapped[uuid.UUID] = mapped_column(UUID)
    name: Mapped[str] = mapped_column(Text)
    prefix: Mapped[str] = mapped_column(Text, unique=True)
    key_hash: Mapped[bytes] = mapped_column(LargeBinary)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    last_used_at: Mapped[datetime | None] = mapped_column(TS)
    expires_at: Mapped[datetime | None] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID)
    pos_integration_id: Mapped[uuid.UUID] = mapped_column(UUID)
    url: Mapped[str] = mapped_column(Text)
    secret_enc: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class StaticQr(Base):
    __tablename__ = "static_qr_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID)
    code: Mapped[str] = mapped_column(Text, unique=True)
    label: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class PaymentLink(Base):
    __tablename__ = "payment_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID)
    code: Mapped[str] = mapped_column(Text, unique=True)
    amount_cents: Mapped[int | None] = mapped_column(BigInteger)
    description: Mapped[str] = mapped_column(Text)
    order_ref: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(TS)
    max_uses: Mapped[int | None] = mapped_column(Integer)
    paid_count: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class PaymentRequest(Base):
    __tablename__ = "payment_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID)
    checkout_token: Mapped[str] = mapped_column(Text, unique=True)
    source: Mapped[str] = mapped_column(Text)
    static_qr_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    payment_link_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    pos_integration_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    external_order_id: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    request_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(Text, default="NZD")
    description: Mapped[str | None] = mapped_column(Text)
    reference: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(Text, default="open")
    status_reason: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(TS)
    paid_attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    paid_at: Mapped[datetime | None] = mapped_column(TS)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID)
    payment_request_id: Mapped[uuid.UUID] = mapped_column(UUID)
    provider: Mapped[str] = mapped_column(Text)
    live: Mapped[bool] = mapped_column(Boolean, default=False)
    provider_payment_id: Mapped[str | None] = mapped_column(Text, unique=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    reference: Mapped[str] = mapped_column(Text)
    destination_account_id: Mapped[uuid.UUID] = mapped_column(UUID)
    destination_name: Mapped[str] = mapped_column(Text)
    destination_account_enc: Mapped[str] = mapped_column(Text)
    destination_last4: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="CREATED")
    status_reason: Mapped[str | None] = mapped_column(Text)
    provider_status: Mapped[str | None] = mapped_column(Text)
    provider_status_code: Mapped[str | None] = mapped_column(Text)
    authorisation_url: Mapped[str | None] = mapped_column(Text)
    provider_expires_at: Mapped[datetime | None] = mapped_column(TS)
    last_event_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    authorised_at: Mapped[datetime | None] = mapped_column(TS)
    sent_at: Mapped[datetime | None] = mapped_column(TS)
    terminal_at: Mapped[datetime | None] = mapped_column(TS)
    last_polled_at: Mapped[datetime | None] = mapped_column(TS)
    next_poll_at: Mapped[datetime | None] = mapped_column(TS)
    poll_count: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=0)


class ProviderEvent(Base):
    __tablename__ = "payment_provider_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(Text)
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    provider_payment_id: Mapped[str | None] = mapped_column(Text)
    dedup_key: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    signature_valid: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str | None] = mapped_column(Text)
    status_code: Mapped[str | None] = mapped_column(Text)
    emitted_at: Mapped[datetime | None] = mapped_column(TS)
    received_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    raw_body: Mapped[str] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(TS)
    outcome: Mapped[str | None] = mapped_column(Text)


class Reconciliation(Base):
    __tablename__ = "payment_reconciliations"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID)
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUID)
    method: Mapped[str] = mapped_column(Text)
    from_status: Mapped[str] = mapped_column(Text)
    to_status: Mapped[str] = mapped_column(Text)
    provider_status: Mapped[str | None] = mapped_column(Text)
    provider_code: Mapped[str | None] = mapped_column(Text)
    operator_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID)
    endpoint_id: Mapped[uuid.UUID] = mapped_column(UUID)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID)
    event_type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
    delivered_at: Mapped[datetime | None] = mapped_column(TS)


class PaymentEvent(Base):
    __tablename__ = "payment_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID)
    payment_request_id: Mapped[uuid.UUID] = mapped_column(UUID)
    event_type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TS, server_default="now()")
