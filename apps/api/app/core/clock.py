"""Current time, in one place, so tests can move the clock (delayed results, expiry)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

_offset = timedelta(0)


def now() -> datetime:
    return datetime.now(UTC) + _offset


def advance(delta: timedelta) -> None:  # tests only
    global _offset
    _offset += delta


def reset() -> None:  # tests only
    global _offset
    _offset = timedelta(0)
