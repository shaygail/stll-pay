"""Identifiers.

UUIDv7 (RFC 9562) keeps primary keys roughly time-ordered, which keeps indexes compact.
Python 3.13 has no ``uuid.uuid7``, so it is built here from the RFC layout.
"""

from __future__ import annotations

import secrets
import time
import uuid

# Crockford base32 without I, L, O, U, so references read clearly aloud and on receipts.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def uuid7() -> uuid.UUID:
    ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(secrets.token_bytes(10), "big")
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76  # version
    value |= ((rand >> 62) & 0xFFF) << 64  # rand_a, 12 bits
    value |= 0b10 << 62  # variant
    value |= rand & ((1 << 62) - 1)  # rand_b, 62 bits
    return uuid.UUID(int=value)


def random_code(length: int) -> str:
    """Random Crockford base32 string, used for payment references (Phase 4)."""
    return "".join(secrets.choice(_CROCKFORD) for _ in range(length))
