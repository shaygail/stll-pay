"""Platform-wide switches, stored in the database so an admin can change them at runtime.

Launch state: multi_merchant_enabled = false. Only STLL HAUS operates; public sign-up and
self-service merchant creation are closed, and staff join by invitation.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def multi_merchant_enabled(s: AsyncSession) -> bool:
    value = (await s.execute(text("SELECT multi_merchant_enabled FROM platform_settings"))
             ).scalar()
    return bool(value)
