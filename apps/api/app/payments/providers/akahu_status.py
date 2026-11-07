"""Map Akahu one-off payment statuses to STLL PAY statuses.

Statuses and reason codes are verbatim from
https://developers.akahu.nz/docs/oneoff-payments-lifecycle (read 2026-11-07).
See docs/AKAHU_INTEGRATION.md section 3. The mock provider uses the same vocabulary.
"""

from __future__ import annotations

from app.payments.states import Status

KNOWN = {
    "READY": {None},
    "AUTHORISED": {None},
    "SUBMITTED": {"PENDING", "SETTLEMENT_IN_PROGRESS"},
    "SENT": {None},
    "CANCELLED": {"TIMED_OUT", "USER_CANCELLED", "APP_CANCELLED"},
    "FAILED": {"REJECTED", "BANK_ERROR", "BANK_ERROR_INDETERMINATE", "INTERNAL_ERROR"},
}


def normalise_akahu(status: str, code: str | None) -> Status:
    if status == "READY":
        return Status.AWAITING_AUTHORISATION
    if status == "AUTHORISED":
        return Status.AUTHORISED
    if status == "SUBMITTED":
        return Status.PROCESSING
    if status == "SENT":
        return Status.SENT
    if status == "CANCELLED":
        return Status.EXPIRED if code == "TIMED_OUT" else Status.CANCELLED
    if status == "FAILED":
        # The bank may or may not have processed it. Never treat as a clean failure:
        # retrying could charge the customer twice.
        if code == "BANK_ERROR_INDETERMINATE":
            return Status.REQUIRES_REVIEW
        if code in KNOWN["FAILED"]:
            return Status.FAILED
    # Anything undocumented needs a human, not a guess.
    return Status.REQUIRES_REVIEW
