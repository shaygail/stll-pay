# Payment lifecycle

How a payment moves from "the POS wants $12.50" to "the bank confirmed it". Code:
`apps/api/app/payments/` (`states.py`, `service.py`, `worker.py`).

## The rule that matters most

**Only verified provider information says money moved.** That means:
- a signature-checked webhook, confirmed by fetching the payment from the provider;
- the provider's answer to a poll;
- or an audited platform-admin resolution backed by evidence.

The customer returning from their bank changes nothing. The return page only reads status
(it may ask the API to check with the provider).

## Requests and attempts

- A **payment request** is what someone wants paid: amount, merchant, reference, expiry.
  It comes from a counter QR (`static_qr`), a POS (`dynamic_qr`), a payment link (`link`)
  or the dashboard (`dashboard`).
- A **payment attempt** is one try at the bank for that request. A customer who cancels,
  or whose bank declines, can try again; that creates a new attempt.

The database guarantees:
- **At most one attempt in flight per request**, so a double tap can't start two bank payments.
- **At most one successful attempt per request.**
- **Amount and destination are immutable.** Each attempt keeps an encrypted copy of the
  merchant's verified account taken when it started. Changing the bank account later never
  affects it, and triggers block edits even by the app's own database role.

## Attempt statuses

| Status | Meaning | Customer sees |
|---|---|---|
| `CREATED` | Recorded before calling the provider | Waiting |
| `AWAITING_AUTHORISATION` | Provider payment exists; customer is choosing or approving at their bank | Waiting (can resume) |
| `AUTHORISED` | Customer approved at their bank | Processing · not paid yet |
| `PROCESSING` | Bank is sending it | Processing · not paid yet |
| `SENT` | **Provider confirmed settlement.** The request becomes `paid` | Paid · confirmed by your bank |
| `SETTLED_OR_RECONCILED` | Matched on the merchant's statement (later phase; needs Akahu Tier 2) | Paid |
| `FAILED` | Declined or errored; no money moved | Didn't go through, try again |
| `CANCELLED` | Customer or app cancelled before approving | Cancelled, pay again |
| `EXPIRED` | Not approved in time | Expired |
| `REQUIRES_REVIEW` | Money may or may not have moved | Unconfirmed, **don't pay again** |

Allowed moves (anything else is ignored as stale, or flagged as a conflict):

```
CREATED ─────────► AWAITING_AUTHORISATION ─► AUTHORISED ─► PROCESSING ─► SENT ─► SETTLED_OR_RECONCILED
   │                    │   │   │                │              │            │
   └► FAILED            │   │   └► CANCELLED      └──► FAILED ◄──┘            └► REQUIRES_REVIEW
   └► REQUIRES_REVIEW   │   └────► EXPIRED
                        └────────► FAILED / REQUIRES_REVIEW / straight to PROCESSING or SENT
REQUIRES_REVIEW ─► SENT | FAILED | CANCELLED   (later provider truth, or admin with evidence)
```

`FAILED`, `CANCELLED`, `EXPIRED` and `SETTLED_OR_RECONCILED` never change again; the
database enforces this. Once the customer has approved (`AUTHORISED` or later), nothing
can cancel or expire the payment.

### How provider updates are applied

The provider reports its own status (Akahu's vocabulary, used by the mock too). It's mapped
as in [AKAHU_INTEGRATION.md §3](AKAHU_INTEGRATION.md#3-payment-lifecycle-verbatim-statuses),
then `decide(current, target)` picks one of:
- **apply**: a legal forward move.
- **same**: a duplicate or replay; nothing changes.
- **stale**: an older status arriving late (out of order); ignored.
- **conflict**: contradicts something final or approved (for example "cancelled" after
  approval). The attempt goes to `REQUIRES_REVIEW`, or, if it's already final, the request
  is flagged.

The provider's amount and payee are also compared with our snapshot. A mismatch goes to review.

## Request statuses

| Status | When |
|---|---|
| `open` | Waiting for a successful attempt (retries allowed) |
| `paid` | An attempt reached `SENT`. Emits `payment.succeeded` (once) |
| `cancelled` | Merchant or POS cancelled before the customer approved. Emits `payment.cancelled` |
| `expired` | Time ran out with nothing approved. Emits `payment.expired` |
| `requires_review` | Unclear bank result, payment after close, duplicate payment, or provider conflict. Emits `payment.requires_review`. Resolving it to `SENT` makes it `paid` and emits `payment.succeeded` |

Each event type is recorded at most once per request (`payment_events` is unique on
request and type), so a POS can't be told "paid" twice for one order.

## Timeline of one payment

1. **Request created** (POS, counter, link or dashboard). The amount is checked on the server.
2. **Customer taps "Pay with bank".** Under a row lock on the request, the attempt is
   recorded as `CREATED`, with its destination snapshot, *before* the provider is called.
3. **Provider called with no locks held.** It returns an authorisation URL and the attempt
   becomes `AWAITING_AUTHORISATION`. The provider payment's expiry is at least 15 minutes
   (Akahu's minimum); our request can expire sooner, and then we cancel it at the provider.
4. **Customer approves at their bank.**
5. **The provider sends signed webhooks.** For each one: verify the signature, deduplicate,
   then **re-fetch** the payment and apply that. The webhook body itself is never trusted.
6. **The worker polls** anything in flight at 5 s, 10 s, 20 s, 30 s, 60 s, 120 s, then every
   5 minutes. This covers lost webhooks.
7. **`SENT`:** the request becomes `paid` and the POS webhook is queued and delivered with retries.

## Failure handling

| Situation | What happens |
|---|---|
| Provider down or 5xx on create | Attempt `FAILED` (no link ever reached the customer, so nothing can be charged). Customer can try again |
| Provider times out on create | Same: a payment the customer never received a link for can't be approved, and expires at the provider |
| Crash between recording and calling the provider | Attempt stays `CREATED`. A retry gets "starting, try again". After 5 minutes the worker fails it, then retries work |
| Provider down while polling | Nothing changes; the next poll is scheduled |
| Webhooks lost | Polling finds the result |
| Webhook replayed | Deduplicated (stored with a unique key) and acknowledged |
| Webhooks out of order | Older statuses are ignored as stale |
| Forged webhook | Signature check fails, so 401. Even a valid signature only triggers a re-fetch |
| `BANK_ERROR_INDETERMINATE` | `REQUIRES_REVIEW`. **Never retried automatically** (it could charge twice). The customer is told not to pay again; an admin resolves it with evidence |
| Approved but no final result after 24 h | `REQUIRES_REVIEW` |
| Request expires while the customer is at the bank | Cancelled at the provider if still unapproved. If they approved just in time, the payment keeps tracking and completes normally |
| Cancel requested after the customer approved | Refused (409) |
| Paid after the request was closed | Request goes to `requires_review` (for example the POS took cash meanwhile) |
| Second successful payment for one request | Second attempt goes to `REQUIRES_REVIEW` (`duplicate_payment`), never hidden |

## Worker

`uv run python -m app.worker` loops every 2 seconds:
- Deliver the mock provider's webhooks (development only).
- Poll due attempts.
- Recover stuck attempts.
- Expire due requests.
- Deliver POS webhooks.

All of this state lives in the database, and rows are claimed with `FOR UPDATE SKIP LOCKED`,
so restarts and multiple workers are safe.

## Testing without money

The mock provider follows Akahu Demo Bank's magic amounts. See
`app/payments/providers/mock.py` and [TESTING.md](TESTING.md).
