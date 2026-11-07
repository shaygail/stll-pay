"""Database engine, per-request transactions and tenant context.

Every request runs in one transaction. Row-level security policies read the tenant
context from transaction-local settings (``app.user_id``, ``app.merchant_id``,
``app.is_platform_admin``), which ``set_context`` sets with ``set_config(..., true)``.
The settings disappear when the transaction ends, so a pooled connection never carries
one request's tenant into the next.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database_url, pool_pre_ping=True, pool_size=10)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one transaction per request, committed on success."""
    async with get_sessionmaker()() as session, session.begin():
        yield session


async def set_context(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None = None,
    merchant_id: uuid.UUID | None = None,
    is_platform_admin: bool | None = None,
) -> None:
    """Set transaction-local tenant context used by row-level security."""
    values: dict[str, str] = {}
    if user_id is not None:
        values["app.user_id"] = str(user_id)
    if merchant_id is not None:
        values["app.merchant_id"] = str(merchant_id)
    if is_platform_admin is not None:
        values["app.is_platform_admin"] = "true" if is_platform_admin else "false"
    for key, value in values.items():
        await session.execute(
            text("SELECT set_config(:key, :value, true)"), {"key": key, "value": value}
        )


async def clear_merchant_context(session: AsyncSession) -> None:
    await session.execute(text("SELECT set_config('app.merchant_id', '', true)"))
