"""Akahu one-off payment wire format, shared by the Akahu and mock providers.

Source: https://developers.akahu.nz/docs/oneoff-payments-monitoring-progress and the API
reference (read 2026-11-07). Webhooks are signed with RSA-SHA256 over the raw body; the
base64 signature and key id arrive in these headers.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.payments.providers.base import WebhookNotice, WebhookRejected

SIGNATURE_HEADER = "x-akahu-payments-signature"
KEY_HEADER = "x-akahu-payments-signing-key"
WEBHOOK_TYPE = "ONE_OFF_PAYMENT_STATUS_UPDATE"


def verify_signature(public_key: Any, raw_body: bytes, signature_b64: str) -> None:
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise WebhookRejected("signing key is not RSA")
    try:
        public_key.verify(base64.b64decode(signature_b64, validate=True), raw_body,
                          padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError) as exc:
        raise WebhookRejected("bad signature") from exc


def parse_notice(raw_body: bytes) -> WebhookNotice:
    try:
        body = json.loads(raw_body)
        if body["type"] != WEBHOOK_TYPE:
            raise WebhookRejected("unexpected webhook type")
        reason = body.get("status_reason") or {}
        emitted = body.get("emitted_at")
        return WebhookNotice(
            provider_payment_id=str(body["_payment"]),
            status=body.get("status"),
            status_code=reason.get("code"),
            emitted_at=parse_time(emitted) if emitted else None,
        )
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise WebhookRejected("malformed body") from exc


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def cents_to_dollars(cents: int) -> float:
    """Akahu takes dollars as a JSON number. Exact for 2 decimal places."""
    return float(Decimal(cents) / 100)


def dollars_to_cents(amount: Any) -> int:
    """Parse via the decimal string, never float arithmetic (0.29 * 100 != 29 in floats)."""
    return int((Decimal(str(amount)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
