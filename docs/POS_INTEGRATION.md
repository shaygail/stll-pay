# POS integration API (v1)

For STLL POS (Phase 7) and other point-of-sale systems. STLL PAY is a separate service. It
doesn't replace the POS checkout and never changes other payment methods.

## Set up (merchant dashboard, owner or manager, needs a fresh MFA code)

1. **Create an integration** (for example "STLL POS"). Its `enabled` switch is the feature
   flag: switching it off immediately refuses its API keys, and other payment methods are
   unaffected.
2. **Create an API key.** It's shown **once** and stored only as a hash. Format:
   `sp_test_xxxxxxxx_…` (or `sp_live_…` in production).
3. **Add a webhook endpoint** (https). Its signing secret (`whsec_…`) is shown once.

## Requests

All requests use `Authorization: Bearer <api key>`. All amounts are **integer NZD cents**.

### Create a payment request

```
POST /v1/pos/payment-requests
Idempotency-Key: <unique per attempt to create, e.g. order id + till session>
{
  "external_order_id": "A-42",
  "amount_cents": 1250,
  "description": "Order 42",
  "expires_in_seconds": 900        // 60-3600, default 900
}
```

**Response 201:** `{ id, reference, status: "open", amount_cents, checkout_url, expires_at, … }`.
Show `checkout_url` as a QR code.

| Situation | Result |
|---|---|
| Same `Idempotency-Key` and same body | The same request again (safe retry after a timeout) |
| Same key, different body | `409 idempotency_conflict` |
| Order already has an open, paid or review request | `409 order_has_payment_request`, with `details.payment_request_id` |
| No `Idempotency-Key` | `400` |
| Amount not a positive integer within limits | `422` |

### Read and cancel

```
GET  /v1/pos/payment-requests/{id}
GET  /v1/pos/payment-requests?external_order_id=A-42
POST /v1/pos/payment-requests/{id}/cancel
```

Cancel works only before the customer approves at their bank. After that it returns
`409 payment_in_progress`: wait for the result.

## Webhooks to the POS

Sent for request outcomes, **at most once per request per event type**:
- `payment.succeeded`
- `payment.cancelled`
- `payment.expired`
- `payment.requires_review`

```
POST <your endpoint>
STLL-Event-Id: <uuid>
STLL-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>.<raw body>")>
{"id": "...", "type": "payment.succeeded", "created_at": "...",
 "data": {"payment_request": {"id", "reference", "status", "amount_cents",
          "external_order_id", "paid_at", ...}, "attempt": {...}}}
```

**Verify every webhook:**
1. Recompute the HMAC over the raw body and compare in constant time.
2. Reject timestamps older than 5 minutes.
3. Deduplicate on `STLL-Event-Id`.

Reply with 2xx within 10 seconds. Failed deliveries retry with backoff (10 s up to 24 h,
12 tries).

## Recommended POS success policy

- **Complete the order only on `payment.succeeded`, or when `GET` shows `status: "paid"`.**
  That means the provider confirmed the payment was sent.
- `payment.requires_review` means **don't hand over goods as paid, and don't ask the
  customer to pay again**. Check the dashboard.
- If webhooks are delayed, poll `GET` every few seconds while the QR is on screen.
- Never treat the customer's phone screen as proof of payment.
