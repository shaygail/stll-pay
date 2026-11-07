"""Append-only audit log. Hash chaining happens in the database trigger."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Keys that must never be written to the audit log, even by mistake.
_REDACT = {"password", "token", "secret", "code", "mfa_secret", "account_number", "api_key"}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[redacted]" if k.lower() in _REDACT else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


async def record(
    session: AsyncSession,
    action: str,
    *,
    actor_user_id: uuid.UUID | None = None,
    merchant_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    ip: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    # Plain INSERT without RETURNING: the row may not be readable under the caller's
    # row-level security policy (for example events with no merchant).
    await session.execute(
        text(
            "INSERT INTO audit_logs (actor_user_id, merchant_id, action, target_type, target_id,"
            " ip, metadata, prev_hash, hash) VALUES (:actor, :merchant, :action, :ttype, :tid,"
            " CAST(:ip AS inet), CAST(:meta AS jsonb), '\\x00', '\\x00')"
        ),
        {
            "actor": actor_user_id, "merchant": merchant_id, "action": action,
            "ttype": target_type, "tid": target_id, "ip": ip,
            "meta": json.dumps(_redact(metadata or {}), sort_keys=True, default=str),
        },
    )
