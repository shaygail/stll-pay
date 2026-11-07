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

## Payment test scenarios

The scenario list from the brief (success, cancel, timeout, delayed results, duplicates,
replayed, missing and out-of-order webhooks, expiry, tampered amounts, cross-merchant
access, restarts, reconciliation) is built in Phase 4 with the mock provider. Akahu Demo
Bank scenarios, using its documented magic amounts, come in Phase 5.
