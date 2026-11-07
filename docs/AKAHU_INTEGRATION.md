# Akahu integration: verified findings

**Status:** research only. Nothing here has been run against Akahu yet.
**Source:** developers.akahu.nz, read on 2026-11-07. The pages were last updated
between 2026-06-18 and 2026-10-29. Check them again before Phase 5.

Each fact below links to its source page. Where the docs say nothing, this file says
**not documented**. Those gaps go to Akahu as questions; we do not fill them with guesses.

---

## 1. Which Akahu product STLL PAY needs

STLL PAY needs **one-off payments**, not enduring payments.

- One-off payments "don't require account connectivity and can operate as a stand-alone
  payment solution." The payer does not need an Akahu account or a saved connection.
  ([One-Off Payments](https://developers.akahu.nz/docs/oneoff-payments))
- They always use official open banking connections, not the older "classic" connections.
  ([Demo Bank](https://developers.akahu.nz/docs/demo-bank))
- Akahu lists "Checkout and Point of Sale systems" and "Invoice payments" as typical uses.
  This fits our static QR, dynamic QR and payment link modes.

### App type

| | Personal App | Full App |
|---|---|---|
| Payments | ✖ | ✅ |
| Webhooks | ✖ | ✅ |
| One-off connectivity | ✖ | ✅ |
| Users | 1 (your own account) | Unlimited |
| Pricing | Free | Usage based |

([Personal Apps](https://developers.akahu.nz/docs/personal-apps))

**Consequence:** if the finance tracker uses a **Personal App**, its credentials
**cannot** make payments, so STLL PAY needs a new Full App. You can tell which type it is:
a Personal App authenticates with a *User Access Token* plus an *App ID Token*. The
one-off payments API takes an *App Token* plus an *App Secret*. Since you mention an App
Secret, it may be a Full App, but please confirm in the Akahu dashboard. Either way, STLL
PAY should have **its own** app and credentials, kept apart from the finance tracker.

### Accreditation

One-off payments are **Tier 1**.
([App Accreditation](https://developers.akahu.nz/docs/app-accreditation))

Tier 1 requires:
1. A signed contract for the relevant Akahu services.
2. Background due diligence by Akahu.
3. A review of our privacy notice.
4. A limited app review of the Akahu-related functionality.

Until accreditation, the app runs under **sandbox restrictions**. A production
environment is issued only afterwards. The first app review is free; later reviews cost
NZ$1,000 each, per the same page.

Requirements that apply to us (same page):
- Akahu credentials must never be exposed to the client.
- TLS on all traffic.
- OWASP Top 10 and CIS Controls addressed.
- **Server-validated MFA for all users of the Akahu functionality.** Allowed methods are
  password plus SMS, password plus authenticator, PIN plus SMS, or passkeys with user
  verification. For web sessions longer than 2 hours, MFA status must expire after 2 hours
  before further payments can be initiated.
  → It is **not documented** whether this applies to an anonymous *payer* in a checkout
  flow, or only to the app's own logged-in users (our merchants and staff). **Question for
  Akahu.** Our design applies MFA to every merchant and admin login either way.
- A consumer information page that explains Akahu's role and shows the Akahu logo.

## 2. API surface (one-off payments)

**Base URL:** `https://api.payments.akahu.io/`. This is a different host from the main
`api.akahu.io` API.
**Auth:** HTTP Basic, with App Token as username and App Secret as password. Server side only.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/one-off-payments` | Create a payment. Returns `_id`, `authorisation_url`, `expires_at` |
| `GET` | `/v1/one-off-payments/{paymentId}` | Get a payment and its status |
| `PUT` | `/v1/one-off-payments/{paymentId}/cancel` | Cancel. **Only while the status is `READY`** |
| `GET` | `/v1/webhooks/keys/{keyId}` | Get the webhook public signing key (PEM) |
| `GET` | `/v1/connections` | Banks available for one-off payments, with their limits |

### Create payment: request body

| Field | Req. | Notes |
|---|---|---|
| `amount` | ✅ | **Number in dollars**, rounded to 2 dp. We store integer cents and convert with `Decimal` only at this boundary |
| `redirect_uri` | ✅ | Where the customer returns. **Not proof of payment** |
| `payee.name` | ✅ | Pattern `^[A-Za-z0-9 '\.,&\-_]*$` |
| `payee.account_number` | ✅ | NZ BECS account number |
| `payee.particulars` / `code` / `reference` | | **Under 13 characters**, `[A-Za-z0-9 \-_]`. Shown on the **merchant's** statement and used for reconciliation |
| `payer.particulars` / `code` / `reference` | | Shown on the **customer's** statement, same limits |
| `payer.bank` | | Forces a specific bank |
| `payer.account_number`, `payer.name` | | Forces a specific payer account |
| `payer.release_account_details` | | Asks the bank to release the payer's name and account number after consent. Useful for refunds |
| `webhook_uri` | | Receives status webhooks |
| `expires_at` | | ISO 8601, **at least 15 minutes and at most 1 year** ahead. Default is 1 hour |
| `redirect_mode` | | `"deep_link"` for native app redirects |

([Making a One-Off Payment](https://developers.akahu.nz/docs/oneoff-payments-making-a-payment),
[API reference](https://developers.akahu.nz/reference/createoneoffpayment))

**Design consequences:**
- **The 15-minute minimum expiry** is longer than a POS QR should live. So STLL PAY does
  **not** create the Akahu payment when the QR is shown. It creates it only when the
  customer taps "Pay with bank". If our own request expires while Akahu still says
  `READY`, we call `cancel`.
- **The 12-character reference limit:** our payment reference format is
  `SP` plus 8 Crockford base32 characters (10 characters), for example `SP7K3QX9MD`.
- **Payee details are supplied by us on every request.** For one-off payments the
  documented schema has no `source` or `verification_method` field. Those exist for
  enduring payment consents
  ([Payee Verification](https://developers.akahu.nz/docs/payee-verification)). This means
  STLL PAY carries the responsibility of sending the correct merchant account. Hence our
  immutable destination snapshots and verified merchant bank accounts.

## 3. Payment lifecycle (verbatim statuses)

([One-Off Payment Lifecycle](https://developers.akahu.nz/docs/oneoff-payments-lifecycle))

| Akahu `status` | `status_reason.code` | Meaning | Terminal |
|---|---|---|---|
| `READY` | | Created, awaiting user authorisation | |
| `AUTHORISED` | | User authorised with their bank | |
| `SUBMITTED` | `PENDING` | Accepted by the bank, checks under way or queued | |
| `SUBMITTED` | `SETTLEMENT_IN_PROGRESS` | Checks done, generally expected to succeed | |
| `SENT` | | **Payment settlement has completed** | ✅ |
| `CANCELLED` | `TIMED_OUT` | Not authorised before expiry | ✅ |
| `CANCELLED` | `USER_CANCELLED` | User cancelled | ✅ |
| `CANCELLED` | `APP_CANCELLED` | Cancelled by API call | ✅ |
| `FAILED` | `REJECTED` | Rejected by Akahu (for example fraud check) or by the bank. Insufficient funds also shows as `REJECTED`, because the open banking standard hides the reason | ✅ |
| `FAILED` | `BANK_ERROR` | Error at the bank | ✅ |
| `FAILED` | `BANK_ERROR_INDETERMINATE` | **Unclear whether the payment has been or will be processed** | ✅ |
| `FAILED` | `INTERNAL_ERROR` | Error inside Akahu | ✅ |

### Mapping to STLL PAY internal statuses

| Internal | From Akahu | Notes |
|---|---|---|
| `CREATED` | (none yet) | Our request exists. No Akahu payment created yet |
| `AWAITING_AUTHORISATION` | `READY` | |
| `AUTHORISED` | `AUTHORISED` | |
| `PROCESSING` | `SUBMITTED` (either reason) | UI: "Your bank is processing this payment" |
| `SENT` | `SENT` | **The point where a payment counts as paid.** POS completes the order here (default policy) |
| `SETTLED_OR_RECONCILED` | (none) | Our own later step: matched against the merchant's bank statement. **Needs bank transaction data**, see §6 |
| `CANCELLED` | `CANCELLED/USER_CANCELLED`, `APP_CANCELLED` | |
| `EXPIRED` | `CANCELLED/TIMED_OUT`, or our own expiry | |
| `FAILED` | `FAILED/REJECTED`, `BANK_ERROR`, `INTERNAL_ERROR` | Customer may retry, which creates a **new** attempt |
| `REQUIRES_REVIEW` | `FAILED/BANK_ERROR_INDETERMINATE`; contradictory or unknown data | **No automatic retry.** Shown to the merchant as "Unconfirmed". The customer is told not to pay again until it is resolved |

**Note:** Akahu marks `FAILED/BANK_ERROR_INDETERMINATE` as terminal, but the money may
still move. Retrying it automatically could charge the customer twice, so we treat it
as `REQUIRES_REVIEW`.

## 4. Monitoring: webhooks and polling

([Monitoring One-Off Payment Progress](https://developers.akahu.nz/docs/oneoff-payments-monitoring-progress))

- Akahu sends a webhook on every status change, if `webhook_uri` was set. The payload
  fields are `type` (always `ONE_OFF_PAYMENT_STATUS_UPDATE`), `_payment`, `status`,
  `status_reason.code`, `status_reason.message` and `emitted_at`.
- **There is no `state` field.** We put our attempt ID in the `webhook_uri` query string,
  and still check it against `_payment`.
- **Signature:** `X-Akahu-Payments-Signature` (base64) and `X-Akahu-Payments-Signing-Key`
  (key ID). Fetch the key from `GET /v1/webhooks/keys/{keyId}` and cache it. Verify the
  **raw body** with RSA-SHA256, and discard webhooks that fail.
- Our endpoint must return 2xx **within 10 seconds**. Akahu retries **up to 20 times**
  with exponential backoff.
- **Ordering:** use `emitted_at` to handle out-of-order delivery. Our state machine also
  refuses any move backwards from a terminal state.
- Akahu itself recommends a scheduled fallback check. Our reconciliation worker polls
  `GET /v1/one-off-payments/{id}` for every non-terminal attempt.
- **Our handling:** store the raw event, verify it, deduplicate, then process. Each
  webhook is also **re-confirmed with a `GET`** before we act on it, so a lost, replayed
  or reordered webhook can't put a payment in the wrong state.

**Not documented:** a webhook event ID. Our dedup key is
`(_payment, status, status_reason.code, emitted_at)` plus a body hash. Also not
documented: a signature timestamp for replay windows. Replay protection therefore relies
on the dedup key plus the `GET` re-confirmation.

## 5. Demo Bank (testing without money)

([Demo Bank](https://developers.akahu.nz/docs/demo-bank))

- Available to Full Apps in **non-production** environments through the normal flow.
  Login accepts any username and password. The password `Password1` simulates a
  wrong-password error. MFA is a simple maths question.
- **The payee must be a real NZ bank account number.** A Demo Bank account number as the
  destination fails with HTTP 400. No money moves.
- Magic amounts for **one-off** payments (matched to 2 dp; a leading `105.` is treated as
  `5.`):

| Amount | One-off outcome |
|---|---|
| 5.01 | `500` error on creation |
| 5.02 | `FAILED` / `INTERNAL_ERROR` |
| 5.03 | `FAILED` / `BANK_ERROR` |
| 5.04 | `FAILED` / `BANK_ERROR_INDETERMINATE` |
| 5.15 | `FAILED` / `REJECTED` |
| 5.20 | 30 s delay, then `SENT` |
| 5.22 | 30 s delay, then `FAILED` / `REJECTED` |
| 5.23 | 180 s delay, then `SENT` |
| 5.24 | 180 s delay, then `FAILED` / `REJECTED` |

The other magic amounts (5.11, 5.14, 5.16, 5.17, 5.19) are listed as N/A for one-off
payments. The page notes that Demo Bank "does not fully mimic" live connections.

## 6. Questions that must go to Akahu

1. **Multi-merchant model:** may one STLL PAY Full App create one-off payments to the
   bank accounts of many *independent* merchants (third parties), not only STLL HAUS?
   Does it need a different contract or product (for example a platform or partner
   arrangement)?
2. **Payee verification for one-off payments:** the docs describe `STATIC`,
   `VERIFICATION_TOKEN`, `CLIENT_VERIFIED` and `NONE` payees for **enduring** consents
   only. What verification does Akahu need for one-off payees? Can a merchant verify
   ownership of their account through a one-off account-information flow or an
   account verification token? Who carries liability for a wrong payee?
3. **MFA scope:** does the Tier 1 MFA requirement apply to anonymous payers at checkout,
   or only to our authenticated merchant users?
4. **Reselling:** may STLL PAY charge merchants a subscription or per-transaction fee on
   top of Akahu's fees?
5. **Pricing:** the per-payment fee for one-off payments, any fixed monthly fees, and
   whether sandbox and Demo Bank usage is free. (The pricing page was not readable from
   this environment.)
6. **Settlement confirmation:** `SENT` means "settlement has completed" at the payer's
   bank. Is it final, or can a `SENT` payment be reversed or recalled?
7. **Webhooks:** is there a unique event ID, or a signed timestamp we should use for
   replay protection?
8. **Statement reconciliation:** to confirm funds actually arrived (`SETTLED_OR_RECONCILED`),
   we would read each merchant's own account transactions. That is *enduring account
   information*, which is **Tier 2**. Should this wait for a later phase?
9. **Which banks support one-off payments today**, and their limits (`GET /v1/connections`
   answers this once we have credentials).
10. **Refunds:** is there a supported refund pattern, or does the merchant refund
    manually to the account released with `release_account_details`?
11. **Sandbox restrictions:** what exactly do they limit before accreditation?

## 7. Implementation status (Phase 5)

| Item | Status |
|---|---|
| `AkahuPaymentProvider` (`app/payments/providers/akahu.py`) | Built: create, get, cancel, connections, webhook verification with cached signing keys |
| Amounts | Integer cents inside STLL PAY. Converted to dollars only at the Akahu boundary, through `Decimal` (`akahu_wire.py`) |
| Error handling | 5xx, 429, timeouts and network errors: temporary (customer can retry). 400: permanent, with Akahu's issue code. 401/403: permanent `provider_auth`, logged |
| Statement fields | Payee `particulars` = `STLL PAY`, payee and payer `reference` = our 10-character `SP…` reference |
| Tests against a documented fake | 27 tests (`tests/test_akahu_provider.py`, `tests/fake_akahu.py`) covering the HTTP contract, error mapping, signature checks, and the full engine with webhooks, polling, cancel on expiry, outage and amount mismatch |
| Demo Bank runner | `scripts/demo_bank_run.py`: automatic checks plus interactive magic-amount scenarios. Writes a report to `docs/reports/` |
| Demo Bank results | **Not run yet.** Needs sandbox credentials and network access to `api.payments.akahu.io` |
| Webhooks from real Akahu | Need a public HTTPS URL (staging). Not testable from a local machine; polling covers it meanwhile |

The fake is built from the documentation, so passing against it proves the code matches
the docs, not that the docs match the real API. Only Demo Bank runs show that.
