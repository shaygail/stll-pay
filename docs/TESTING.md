# Testing

STLL PAY is tested without real money. Development and CI use the mock payment provider;
Akahu Demo Bank tests come in Phase 5. Tests run against a **real PostgreSQL database**,
because row-level security, triggers and constraints are part of what is being tested.

## Set up once

Requirements: Docker (or PostgreSQL 16), Python 3.13 with [uv](https://docs.astral.sh/uv/),
Node 22.

```bash
docker compose up -d postgres
```

```bash
cd apps/api && uv sync
```

```bash
cd apps/web && npm ci && npx playwright install chromium
```

`infra/postgres-init.sql` creates two roles (`stllpay_owner` runs migrations,
`stllpay_app` is what the API connects as) and the `stllpay_dev`, `stllpay_test` and
`stllpay_e2e` databases.

## Commands

| Suite | Command | What it covers |
|---|---|---|
| API lint | `cd apps/api && uv run ruff check .` | Style, bugs, security lint (Bandit rules) |
| API tests | `cd apps/api && uv run pytest` | Auth, MFA, sessions, CSRF, lockout, tenancy, RLS, roles, admin, audit chain, settings safety |
| Web lint and types | `cd apps/web && npm run lint && npm run typecheck` | |
| Web unit tests | `cd apps/web && npm test` | Money parsing and formatting, cookie handling |
| End-to-end | `cd apps/web && npm run e2e -- --trace off` | Real API and production web build, desktop and phone |
| Dependency audit | `cd apps/api && uv run pip-audit` and `cd apps/web && npm audit --audit-level=high` | Known vulnerabilities |

The API test run rebuilds the `stllpay_test` schema from the migrations each time,
and also runs the downgrade once. The end-to-end run resets `stllpay_e2e` with
`apps/api/scripts/reset_test_db.py`, which refuses to touch any database whose name
doesn't contain `test` or `e2e`.

## What the tests prove (Phase 3)

**Tenant isolation**, through the API and directly in the database as the app role:
- Another merchant's records return 404 for read, update, submit, team and audit.
- With no tenant context, the app role sees no merchants or memberships.
- With merchant B's context, updates to merchant A affect 0 rows, and inserting a membership into A is refused.
- A merchant can't approve itself or switch on live payments, even with direct SQL.
- The app role can't make anyone a platform admin, even with direct SQL.

**Authentication:**
- Password alone never grants access.
- MFA codes can't be replayed.
- The session token is replaced after MFA, and the pre-MFA token stops working.
- Accounts lock after 5 failed passwords.
- Login is rate-limited per email.
- Idle sessions expire.
- Logout revokes the session on the server.
- Session tokens and TOTP secrets are never stored in plain text.

**CSRF:** unsafe requests need the double-submit token, and cross-origin posts are refused.

**Roles:** owner, manager, cashier and viewer each get exactly their permissions. Team changes need a fresh MFA step-up. The last owner can't leave.

**Admin:**
- The admin area returns 404 for non-admins.
- Approval transitions are enforced.
- Live payments can't be enabled while they're globally off.
- Admins need MFA within the last 2 hours.

**Audit log:** append-only for the app role. The hash chain detects an edited row.

**Settings:** production refuses development keys and credentials, plain-HTTP origins, Akahu without credentials, and live payments without Akahu. Live payments are refused outside production.

## Payment scenarios (Phase 4, mock provider)

The mock provider behaves like an external provider. It has its own tables, changes state on
its own schedule, and sends RSA-signed webhooks in Akahu's format. It uses Akahu Demo Bank's
documented magic amounts, plus a few mock-only ones:

| Amount | Outcome |
|---|---|
| 5.01 | Create fails with a server error |
| 5.02 / 5.03 | `FAILED` / internal or bank error |
| 5.04 | `FAILED` / `BANK_ERROR_INDETERMINATE`, so review |
| 5.15 | Declined |
| 5.20 / 5.22 | Sent / declined after 30 s |
| 5.23 / 5.24 | Sent / declined after 180 s |
| 5.05 | Mock only: create times out |
| 5.06 | Mock only: no webhooks (polling must recover) |
| 5.07 | Mock only: every webhook sent twice |
| 5.08 | Mock only: webhooks out of order |

A leading `105.` acts like `5.`, as in Demo Bank. Tests move a controllable clock
(`app/core/clock.py`) rather than waiting.

| Brief scenario | Test |
|---|---|
| Successful payment | `test_payments_flow.py::test_counter_qr_success`, `test_pos_dynamic_qr_success_notifies_pos_once`, e2e |
| Customer cancellation | `test_customer_cancels_at_bank_then_pays`, e2e |
| Payment failure | `test_payment_failure_allows_retry` (4 cases), e2e |
| Bank timeout | `test_bank_timeout_on_create` |
| Delayed success / failure | `test_delayed_success`, `test_delayed_failure`, e2e (processing never shown as paid) |
| Duplicate payment request | `test_pos_idempotency_and_duplicate_orders`, `test_concurrent_pay_taps_create_one_attempt` |
| Replayed webhook | `test_replayed_webhooks_are_ignored` |
| Missing webhook | `test_missing_webhooks_recovered_by_polling` |
| Out-of-order webhook | `test_out_of_order_webhooks_never_go_backwards` |
| Customer closes browser | `test_customer_closes_browser_after_approving` |
| Expired QR / link | `test_expired_pos_qr`, `test_expiry_never_cancels_an_approved_payment`, `test_expired_payment_link` |
| Manipulated amount | `test_manipulated_amounts_rejected` (6 cases), `test_client_cannot_supply_destination_or_amount_at_pay` |
| Invalid bank destination | `test_merchant_without_verified_account_cannot_take_payments`, `test_bank_account_change_never_affects_existing_payment`, `test_only_admin_can_verify_bank_accounts` |
| Cross-merchant access | `test_cross_merchant_access`, `test_rls_hides_other_merchants_payments` |
| Duplicate order completion | `test_pos_dynamic_qr_success_notifies_pos_once`, `test_replayed_webhooks_are_ignored` |
| Provider downtime | `test_provider_create_error_is_clean_failure`, `test_provider_downtime_during_polling_changes_nothing` |
| Server restart during processing | `test_server_restart_during_processing`, `test_crash_before_provider_answers_is_recovered` |
| Reconciliation failure | `test_indeterminate_bank_error_goes_to_review_and_is_never_retried`, `test_no_final_status_after_a_day_needs_review` |

Also covered:
- Forged webhooks.
- POS webhook retries and signatures.
- Cancelling while the customer is at the bank, and refusing to cancel after approval.
- API key revocation and the integration on/off switch.
- Step-up for bank and integration changes.
- Cashier limits.
- Immutable amounts and destinations in the database.
- The Akahu status mapping, and the state machine rules.

Akahu Demo Bank runs of the same scenarios come in Phase 5.

## Launch mode (single merchant)

`tests/test_single_merchant.py` covers launch mode:
- Sign-up is closed, and so is creating businesses.
- The platform switch is admin-only (refused through the API and in the database) and audited.
- Staff join by invitation: invitations are single-use and enforce the password policy, and
  MFA is set up at first login.
- The bootstrap script is idempotent, verifies the account only with evidence, and refuses
  non-ANZ accounts.

The rest of the API suite turns the switch on, because it creates its own users and
merchants. The browser tests run in launch mode against `scripts/bootstrap_stll_haus.py`.
