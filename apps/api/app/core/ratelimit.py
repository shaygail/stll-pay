"""Simple sliding-window rate limiter.

In-process memory, so limits apply per API instance. That is enough for the MVP's single
instance; move to a shared store (Postgres or Redis) before running several instances.
Account lockout (users.failed_login_count) is stored in the database and is shared.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from app.core.config import get_settings
from app.core.errors import ApiError


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window_seconds: int) -> None:
        if not get_settings().rate_limit_enabled:
            return
        now = time.monotonic()
        hits = self._hits[key]
        while hits and hits[0] <= now - window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            retry = int(window_seconds - (now - hits[0])) + 1
            raise ApiError(429, "rate_limited", "Too many attempts. Try again shortly.",
                           {"retry_after": retry})
        hits.append(now)

    def reset(self) -> None:
        self._hits.clear()


limiter = RateLimiter()
