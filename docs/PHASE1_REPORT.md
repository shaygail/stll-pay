# STLL PAY: Phase 1 research and audit

**Date:** 2026-11-07 · **Status:** approved 2026-11-07 (hosting: Supabase Postgres in Sydney) · **No application code written.**

Contents:
1. Audit of the existing projects
2. Design system findings
3. Akahu summary
4. Repository decision
5. Architecture
6. Database
7. MVP scope
8. Roadmap
9. Testable without real money
10. Risks, costs and limits
11. Decisions needed

---

## 1. Audit of existing projects

Both repos were cloned read-only. **Nothing in them was changed.**

### STLL POS: `shaygail/stllhaus-pos`

| | |
|---|---|
| Parts | FastAPI backend (Railway), Next.js 14 web till (Vercel), inventory app, **native SwiftUI iPad register** |
| Backend | FastAPI, SQLAlchemy 2 async, Postgres on Railway (README still says SQLite) |
| Payment methods | Free-text strings on `sales.payment_method`: `"Cash"`, `"EFTPOS"`, `"Bank Transfer"`. iPad `FastPayPaymentOption` also has ANZ tap and card variants. The till **only records** the method; nothing is integrated |
| Money | `Float` columns (`subtotal`, `discount`, `price`) |
| Links to REWARDS | REWARDS sends web orders to the POS `/preorder` endpoint. POS posts status to REWARDS `/api/pos/webhook` with a shared-secret header |

**🔴 Security finding:** `backend/.env` is **committed to the repo**. It holds what looks like
the production Railway `DATABASE_URL` (with password) and the JWT `SECRET_KEY`. Anyone with
read access to the repo, or its history, has them. Recommended steps (I did not do these):
1. Rotate the Railway Postgres password and the JWT `SECRET_KEY`.
2. Remove the file from git and add it to `.gitignore`.
3. Treat the old values as compromised even after the file is removed, because git history keeps them.

**What it means for STLL PAY:** POS integration (Phase 7) adds one new payment option,
"STLL PAY", behind a feature flag. It does not change the existing options. The POS stores
only the STLL PAY payment ID and the final status. The POS `Float` money columns stay as
they are; STLL PAY sends integer cents, and the POS adapter converts them.

### STLL REWARDS: `shaygail/stll-haus-app` (app.stllhaus.co)

| | |
|---|---|
| Stack | Next.js **16.3**, React 19, Tailwind **4**, Prisma 7, Supabase Postgres and Auth, Vercel (`syd1`), Vitest |
| Note | README: "No Stripe / card payments in this platform." STLL PAY stays separate and does not touch loyalty logic |
| Reusable | Design tokens, Geist setup, theme script, `FlowPrimitives` / `VerticalStepper` patterns, `QrMark`, `BrandMark` |

The finance tracker and the Akahu credentials are **not** in either repo, and no STLL
REWARDS repo exists separately (this is it).

## 2. Design system: match app.stllhaus.co

Taken from `stll-haus-app/app/globals.css` and `docs/REDESIGN_REPORT.md`. STLL PAY will
**copy these tokens exactly**.

| Token | Light | Dark |
|---|---|---|
| `--bg` | `#f5f3ef` | `#121211` |
| `--surface` / `--surface-2` | `#ecebe6` / `#e3e1db` | `#1b1b19` / `#252522` |
| `--ink` / `--ink-2` / `--muted` | `#1b1b19` / `#55534e` / `#8f8c85` | `#f2f0eb` / `#b4b1aa` / `#7d7a74` |
| `--line` / `--line-strong` | ink at 10% / 22% | |
| `--sage` (single accent) | `#7a8b78` | `#8fa18c` |
| `--danger` | `#a3473f` | `#e08b82` |
| Radius | 4px controls, 2px tags | |
| Shadows | **none** | |
| Type | **Geist** (Satoshi deferred in REWARDS), headings weight 500, letter-spacing −0.02em, small caps eyebrow labels at 11px | |
| Touch | 44px minimum targets, 16px inputs (stops iOS zoom), 1.5px ink focus outline, honours reduced motion | |
| Theme | Light / Dark / System, applied before first paint from `localStorage` | |

**Satoshi:** REWARDS deferred it. I suggest the same, so the two apps look identical. Add
Satoshi later only once its licence is confirmed for this use.

**Payment-specific additions** (new, in the same style):
- Status colours: sage for "Paid", `--ink-2` for "Processing", `--danger` for "Failed".
  Each also has a text label, never colour alone.
- A large tabular-figures amount display.
- A merchant identity block: name, verified tick, NZBN.

**Live site check:** I couldn't load signed-in dashboard screens. If you'd like the
design matched against the live UI as well as the code, send screenshots of the dashboard
on mobile and desktop. The code tokens are enough to start Phase 2.

## 3. Akahu summary

Full detail with sources: [AKAHU_INTEGRATION.md](AKAHU_INTEGRATION.md).

- One-off payments need a **Full App** (Personal Apps cannot make payments) and **Tier 1
  accreditation**: a contract, due diligence, privacy notice review and a limited app review.
- API: create, get and cancel payments at `api.payments.akahu.io`, with Basic auth.
  Webhooks are signed with RSA-SHA256. Demo Bank has documented magic amounts.
- **Big open question:** whether Akahu permits paying many independent merchants from one
  STLL PAY app. Until Akahu confirms in writing, **only STLL HAUS** goes live; other
  merchants stay in restricted or sandbox mode.

## 4. Repository decision

**STLL PAY gets its own repository (`stll-pay`).** Reasons:
- **Different risk.** Payment code needs its own access controls, CI security checks and
  audit trail. Akahu reviews it as an app.
- **Different deploys.** A POS or REWARDS deploy should never redeploy payments, and the
  reverse.
- **Different data.** STLL PAY needs its own database. It must not share the POS or
  REWARDS databases.

The tokens are **copied** into `stll-pay` for now, with a note saying where they came from.
A shared `@stll/ui` package can come later if a third app needs them.

## 5. Proposed architecture

```
                 ┌──────────────── pay.stllhaus.co (Vercel, syd1) ────────────────┐
 Customer phone ─┤ Next.js 16 · /pay/{slug} · /c/{checkoutId} · /l/{linkId}       │
 Merchant/admin ─┤ /dashboard · /admin   (BFF: httpOnly session cookie only)      │
                 └───────────────┬─────────────────────────────────────────────────┘
                                 │ HTTPS (server to server)
        ┌────────────────────────▼──── api.pay.stllhaus.co (FastAPI, Sydney host) ───┐
STLL POS┤ /v1 integration API (API keys)    │  domains: merchants · auth · payments  │
 ──────►│ /webhooks/akahu (RSA verify)      │  providers: Mock | Akahu               │
        │ worker: poll · expire · webhook-out · reconcile (same codebase, own process)│
        └───────────┬───────────────────────────────────────┬─────────────────────┘
                    │                                       │ Basic auth, server side only
        Supabase Postgres (own project, Sydney)     api.payments.akahu.io
```

**Key choices:**
- **Frontend:** Next.js and Tailwind 4, matching REWARDS. The browser talks only to the
  Next.js server (BFF). All secrets and payment logic live in FastAPI.
- **Backend:** FastAPI, SQLAlchemy 2, Alembic and Pydantic v2, matching the POS backend.
  It is one deployable with clear modules (`merchants`, `auth`, `payments`, `providers`,
  `integrations`, `admin`, `audit`). There are no microservices.
- **Background jobs:** a worker process running a **Postgres-backed job queue**
  (`SELECT … FOR UPDATE SKIP LOCKED`). There is no Redis, so there is one less thing to run.
- **Auth:** owned by the backend. Passwords are hashed with Argon2id. Sessions are stored
  server side in an httpOnly, SameSite=Lax cookie. MFA is **mandatory** for every merchant
  and admin login: TOTP now, passkeys later. MFA status expires after 2 hours before
  payment-sensitive actions, per Akahu. Changing bank details needs step-up re-auth.
  - *Why not reuse Supabase Auth from REWARDS?* REWARDS customer accounts are a separate
    audience from merchant operators. Keeping payment auth in the payment service means
    the full auth path can be audited in one place.
- **Tenant isolation:** two layers.
  1. Every tenant table has `merchant_id`. All queries go through a repository layer
     that takes a `MerchantContext`.
  2. **Postgres row-level security** keyed on `SET LOCAL app.merchant_id`, so even a
     buggy query cannot read another merchant's rows.
  Cross-tenant tests run against **both** layers.
- **Provider interface:**
  ```python
  class PaymentProvider(Protocol):
      async def create_payment(self, req: ProviderPaymentRequest) -> ProviderPayment
      async def get_payment(self, provider_payment_id: str) -> ProviderPayment
      async def cancel_payment(self, provider_payment_id: str) -> None
      async def verify_webhook(self, raw_body: bytes, headers: Mapping[str, str]) -> VerifiedEvent
      def normalise_status(self, status: str, reason: str | None) -> InternalStatus
  ```
  `MockPaymentProvider` has a scriptable scenario engine and a hosted fake bank page.
  `AkahuPaymentProvider` is **off** unless `PAYMENTS_PROVIDER=akahu` and the credentials
  are set. Live payments also need `LIVE_PAYMENTS_ENABLED=true` **and** a per-merchant
  `live_enabled` flag set by a platform admin.
- **Environments:**
  - `dev`: mock provider, synthetic data.
  - `staging`: Akahu sandbox and Demo Bank.
  - `prod`: Akahu production, live payments flag **off** by default.
  Each environment has its own database and secrets.

### 5a. Hosting decision (approved)

- **Database:** a **dedicated Supabase project** for STLL PAY in `ap-southeast-2` (Sydney),
  separate from STLL REWARDS. It is used **as plain Postgres only**:
  - The Data API (PostgREST) is **disabled**, so there are no anon or public keys for STLL PAY.
  - Supabase Auth and Storage are not used.
  - SSL is enforced, and network restrictions allow only the API and worker hosts.
  - Point-in-time recovery is on before any live payments.
  - MFA is on for every Supabase dashboard account.
  - The app connects as a non-owner role. Migrations use a separate owner role.
- **API and worker:** a host with a Sydney region (Fly.io `syd` or AWS `ap-southeast-2`;
  final choice in Phase 3), so database round trips stay local.
- **Web:** Vercel with `preferredRegion = "syd1"`, as STLL REWARDS uses.
- Check the current SOC 2 status and prices on the vendors' trust and pricing pages
  before the Akahu application.

### Checkout flow (dynamic QR)

1. POS calls `POST /v1/payment-requests` (API key plus `Idempotency-Key`) with
   `{order_id, amount_cents, description, expires_in}`. STLL PAY returns
   `{id, checkout_url, qr_svg, expires_at}`.
2. The customer scans and sees the merchant identity and amount, then taps **Pay with bank**.
3. Only then STLL PAY creates a `payment_attempt`. It snapshots the merchant's verified
   account and calls Akahu `POST /v1/one-off-payments` with our reference
   (`SP7K3QX9MD`) and `webhook_uri=…?attempt={id}`. Then it redirects to `authorisation_url`.
4. The customer authorises in their bank and returns to `/c/{id}/return`. That page
   **only shows the current status**; it never sets it. It polls our API.
5. A webhook or the poller confirms `SENT`. STLL PAY marks the request paid and sends a
   **signed** `payment.succeeded` webhook to the POS once. The POS completes the order.

One payment request can have many attempts (for example after a cancel or failure), but
**at most one** attempt can ever reach `SENT` per request. This is enforced by a unique
partial index. If a second attempt somehow succeeds, it goes to `REQUIRES_REVIEW` and
needs a refund.

## 6. Database

PostgreSQL. All money is `BIGINT` cents with a `CHECK (amount_cents > 0)`. All IDs are
UUIDv7, with public IDs that carry a prefix (`pr_`, `pa_`, `mer_`). Timestamps are `timestamptz`.

| Table | Purpose and key constraints |
|---|---|
| `users` | email (unique, citext), password hash, MFA secret (encrypted), `is_platform_admin`, `deleted_at` |
| `merchants` | slug (unique), legal and trading name, NZBN, contact, branding, `status` (`draft·pending_review·restricted·active·suspended`), `live_enabled` |
| `merchant_memberships` | (user, merchant) unique, `role` (`owner·manager·cashier·viewer`) |
| `merchant_bank_accounts` | account number (encrypted, plus a last-4 for display), holder name, `status` (`pending·verified·rejected·retired`), `verified_by/at`. **Only one active verified account per merchant** (partial unique index) |
| `merchant_verifications` | KYB submissions, documents (stored as references only), reviewer and decision |
| `static_qr_codes` | merchant, label, a token that cannot be guessed, `active` |
| `payment_links` | merchant, fixed or open amount, description, reference, expiry, `max_uses`, status |
| `payment_requests` | merchant, source (`static_qr·dynamic_qr·link·manual`), `external_order_id`, `amount_cents`, `currency='NZD'`, reference (unique), status, expiry. **Unique (merchant_id, idempotency_key)** and **unique (merchant_id, pos_integration_id, external_order_id)** |
| `payment_attempts` | request, provider, `provider_payment_id` (unique), **destination snapshot** (name, account, captured at creation and never updated), provider status and reason, internal status, `version` (optimistic lock). **Partial unique: one `SENT` per request** |
| `payment_provider_events` | raw body, headers, signature result, dedup key (unique), `processed_at`, `emitted_at` |
| `payment_reconciliations` | attempt, method (`provider_poll·statement_match·manual_operator`), result, operator, evidence |
| `pos_integrations` | merchant, name, `enabled` (feature flag), settings |
| `api_keys` | merchant, prefix, **hash only**, scopes, `last_used_at`, `revoked_at`, `expires_at` |
| `webhook_endpoints` | merchant, URL, signing secret (encrypted), events, `enabled` |
| `webhook_deliveries` | endpoint, event, attempt count, next retry, response code. Outbox pattern |
| `audit_logs` | actor, merchant, action, target, before and after (redacted), IP, **hash chain** (`prev_hash`). The app role gets **INSERT only**, no UPDATE or DELETE |
| `merchant_subscriptions` | plan code, status. **No billing logic**; it only holds plan placeholders |
| `jobs` | the Postgres job queue |

State changes run as `UPDATE … WHERE id=? AND status IN (allowed_from) AND version=?`
inside a transaction. Every change writes an audit row and an outbox row in the same
transaction.

There is **no** wallet, ledger, balance or payout table. Money moves directly from the
customer's bank to the merchant's bank.

## 7. MVP scope

**In the MVP (live for STLL HAUS only, after accreditation):**
- Static counter QR (customer enters the amount)
- Dynamic QR created from the POS
- Fixed-amount payment links
- Mock provider plus Akahu provider
- Webhook plus polling confirmation
- Merchant dashboard: overview, payments, QR codes, links, team, API keys, settings
- Admin: merchant approval, failed and review queues, audit log
- MFA, RBAC, RLS, audit trail

**Built, but restricted until Akahu confirms the multi-merchant model:**
- Public self-signup and onboarding for other merchants (they can sign up and use mock mode only)

**Later:**
- Manual bank transfer fallback with operator confirmation
- Statement-match reconciliation (needs Tier 2)
- Open-amount links
- Receipts by email
- Passkeys
- Reports export
- External POS API docs portal
- Billing

## 8. Roadmap

Each phase ends with a stop for your approval.

| Phase | Output | Depends on |
|---|---|---|
| **1. Research** (this) | This report and the Akahu findings | none |
| **2. UX/UI** | Information architecture, wireframes, high-fidelity screens for checkout (9 states), dashboard, admin, mobile and iPad | Your OK on §2 |
| **3. Foundation** | Monorepo (`apps/web`, `apps/api`), Docker Compose Postgres, auth and MFA, RBAC, RLS, CI (lint, types, tests, `pip-audit`, `npm audit`, secret scan) | Phase 2 |
| **4. Payment engine** | State machine, mock provider, static and dynamic QR, links, webhook intake, worker, outbound webhooks | Phase 3 |
| **5. Akahu** | `AkahuPaymentProvider`, Demo Bank test suite, test report | Akahu Full App sandbox credentials |
| **6. Merchant platform** | Onboarding, bank verification workflow, admin review, reports | Akahu answer on payee verification |
| **7. POS** | POS adapter behind a flag, iPad and web till QR screen, receipts | Your approval to change the POS |
| **8. Live readiness** | Threat model, security tests, Akahu app review package, controlled real-money test plan | Accreditation |

Phases 2 to 4 and most of 6 can run **now**, without waiting on Akahu.

### 8a. Approved change (2026-11-07): single-merchant MVP launch

After Phase 5, the first release is **STLL HAUS only**:

- **One merchant (STLL HAUS)**, one administrator account, and one verified **ANZ business** receiving account.
- **Public merchant sign-up and onboarding switched off.** The multi-merchant database, tenant isolation and roles stay in place.
- **Priorities:** static QR, dynamic QR (POS), payment links, Akahu, automatic payment status, and the merchant dashboard.
- **A simple interface:** no business switcher, no "add a business" screen, no multi-merchant admin views where one merchant is enough.
- **Payments proven on the mock provider and Akahu Demo Bank first.** Real payments stay off until permissions, approvals and security checks are complete.
- **More merchants later** through an administrator-controlled feature flag, with no rebuild. The data model already supports it.

This reshapes Phase 6 (merchant platform): onboarding is built behind the flag, and only the STLL HAUS path is switched on.

## 9. Testable without real money

**Fully testable now with the mock provider:**
- Every checkout screen and state, including:
  - success, cancel, failure, timeout, delayed success and delayed failure
  - indeterminate result leading to review
- Duplicate requests and idempotency
- Webhook flows: replayed, missing and out-of-order webhooks, plus bad signatures
- Server restart during processing (the worker resumes from the database)
- Provider downtime
- Expiry of QRs and links
- Tampered amounts (the server rejects them)
- Client-supplied destinations (ignored; the snapshot is always used)
- Cross-merchant access (API, UI and RLS)
- RBAC per role
- Duplicate POS completion
- MFA and step-up
- API key rotation
- Audit hash chain
- Playwright on iPhone, Android and iPad viewports with network throttling

**With Akahu sandbox and Demo Bank** (needs Full App credentials): the real API contract,
redirects, every documented magic amount, signing key fetch, signature checks, and
polling recovery. Note that Demo Bank still needs a **real NZ account number** as the
payee, but no money moves.

**Only with real money** (Phase 8, controlled): real bank UIs, real timing, actual
settlement into the STLL HAUS account, and the statement appearance of particulars, code
and reference.

## 10. Risks, costs and limits

| Risk | Impact | Mitigation |
|---|---|---|
| Akahu does not allow third-party merchants | Product stays single-merchant | Ask first (Q1). Architecture already isolates merchants; worst case is STLL HAUS only |
| Accreditation time or rejection | Delays going live | Build to the Tier 1 requirements from day one; prepare the review package in Phase 8 |
| Wrong payee (fraud, typo, account takeover) | Money sent to the wrong person | Verified accounts only, immutable snapshots, step-up plus a 24 h cool-down plus email alert on bank changes, admin approval |
| Indeterminate bank result | Double charge if retried | `REQUIRES_REVIEW`, no automatic retry, clear customer message |
| Customer returns before `SENT` | Staff hand over goods unpaid | POS shows "Processing"; only `SENT` completes the order |
| Regulatory exposure (AML/CFT, FSP registration, CDR regime, Fair Trading, Privacy Act 2020) | Legal risk | **Needs a qualified NZ adviser** before onboarding other merchants. Not covered here |
| Bank coverage or limits | Some customers can't pay | Show the supported banks; keep cash and EFTPOS in the POS |
| POS secret leak (found in §1) | DB compromise | Rotate now |

**Estimated running costs** (rough, before Akahu fees; check current prices):

| Item | Estimate |
|---|---|
| Vercel (Pro, if needed) | ~US$20 / month |
| API plus worker on a Sydney host (for example Fly.io `syd`) | ~US$10–30 / month |
| Supabase Pro project (Sydney) plus point-in-time recovery add-on | ~US$25 / month plus the add-on |
| Sentry | free tier or ~US$26 / month |
| Email (Resend, already used) | free tier |
| **Akahu** | usage-based per payment plus possible fixed fees. **Not known: ask Akahu (Q5)** |

**Operational limits:**
- Payments only work for banks Akahu supports for one-off payments.
- The payment expires after at least 15 minutes on Akahu's side.
- References are capped at 12 characters.
- No refunds through the API is documented.
- Confirming funds actually arrived at the merchant bank needs Tier 2.

## 11. Decisions needed from you

1. **Approve** the architecture (§5), database (§6) and MVP scope (§7).
2. **Approve** copying the STLL REWARDS tokens as the STLL PAY design base, Geist only (§2).
3. **Akahu:** confirm the app type of the finance tracker, and whether you will request a
   new Full App for STLL PAY. Also, should I draft the email to Akahu with the questions
   in [AKAHU_INTEGRATION.md §6](AKAHU_INTEGRATION.md#6-questions-that-must-go-to-akahu)?
4. **POS default success policy:** complete the order on `SENT` (recommended).
5. ~~Hosting~~ **Decided:** Supabase Postgres in Sydney (see §5a). Backend on a Sydney host. Vercel `syd1` for the web app.
6. **POS secret:** will you rotate the committed POS credentials, or would you like me to
   prepare the POS repo change that removes the file? That needs your approval, since it
   touches the POS.
7. Create the empty `stll-pay` GitHub repo so this can be pushed.
