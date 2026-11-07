"""The payment attempt state machine.

Provider status is the only source of truth for money moving. A customer returning from
their bank never changes state; only verified provider information (a signature-checked
webhook confirmed by a GET, or a poll) or an audited admin resolution does.
"""

from __future__ import annotations

from enum import StrEnum


class Status(StrEnum):
    CREATED = "CREATED"
    AWAITING_AUTHORISATION = "AWAITING_AUTHORISATION"
    AUTHORISED = "AUTHORISED"
    PROCESSING = "PROCESSING"
    SENT = "SENT"
    SETTLED_OR_RECONCILED = "SETTLED_OR_RECONCILED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"


IN_FLIGHT = frozenset({Status.CREATED, Status.AWAITING_AUTHORISATION, Status.AUTHORISED,
                       Status.PROCESSING})
# No further changes, ever (the database enforces this too).
FINAL = frozenset({Status.SETTLED_OR_RECONCILED, Status.FAILED, Status.CANCELLED,
                   Status.EXPIRED})
SUCCESS = frozenset({Status.SENT, Status.SETTLED_OR_RECONCILED})
# The customer has approved, so money may move. Never cancel or retry these.
COMMITTED = frozenset({Status.AUTHORISED, Status.PROCESSING, Status.SENT,
                       Status.SETTLED_OR_RECONCILED})

_RANK = {
    Status.CREATED: 0,
    Status.AWAITING_AUTHORISATION: 1,
    Status.AUTHORISED: 2,
    Status.PROCESSING: 3,
}

ALLOWED: dict[Status, frozenset[Status]] = {
    Status.CREATED: frozenset({Status.AWAITING_AUTHORISATION, Status.FAILED,
                               Status.REQUIRES_REVIEW}),
    Status.AWAITING_AUTHORISATION: frozenset({Status.AUTHORISED, Status.PROCESSING, Status.SENT,
                                              Status.FAILED, Status.CANCELLED, Status.EXPIRED,
                                              Status.REQUIRES_REVIEW}),
    Status.AUTHORISED: frozenset({Status.PROCESSING, Status.SENT, Status.FAILED,
                                  Status.REQUIRES_REVIEW}),
    Status.PROCESSING: frozenset({Status.SENT, Status.FAILED, Status.REQUIRES_REVIEW}),
    Status.SENT: frozenset({Status.SETTLED_OR_RECONCILED, Status.REQUIRES_REVIEW}),
    # Review is resolved by later provider truth or an audited admin decision.
    Status.REQUIRES_REVIEW: frozenset({Status.SENT, Status.FAILED, Status.CANCELLED}),
    Status.SETTLED_OR_RECONCILED: frozenset(),
    Status.FAILED: frozenset(),
    Status.CANCELLED: frozenset(),
    Status.EXPIRED: frozenset(),
}


class Decision(StrEnum):
    APPLY = "apply"
    SAME = "same"  # already in this state (duplicate or replay)
    STALE = "stale"  # an older state arriving late (out of order)
    CONFLICT = "conflict"  # contradicts a final or committed state; needs a human


def decide(current: Status, target: Status) -> Decision:
    """Decide what to do when a provider reports ``target`` for an attempt in ``current``."""
    if current == target:
        return Decision.SAME
    if target in ALLOWED[current]:
        # Cancellation can never undo a customer's approval (the table already forbids
        # it), and pending states never override a result.
        return Decision.APPLY
    if target in _RANK and ((current in _RANK and _RANK[target] < _RANK[current])
                            or current in SUCCESS or current in FINAL
                            or current == Status.REQUIRES_REVIEW):
        return Decision.STALE
    return Decision.CONFLICT


# What the customer is told, keyed by attempt status. Wording follows DESIGN_SYSTEM.md §2.
CUSTOMER_VIEW = {
    Status.CREATED: "waiting",
    Status.AWAITING_AUTHORISATION: "waiting",
    Status.AUTHORISED: "processing",
    Status.PROCESSING: "processing",
    Status.SENT: "paid",
    Status.SETTLED_OR_RECONCILED: "paid",
    Status.FAILED: "failed",
    Status.CANCELLED: "cancelled",
    Status.EXPIRED: "expired",
    Status.REQUIRES_REVIEW: "unconfirmed",
}
