# Database

PostgreSQL 16 (Supabase in Sydney when deployed, used as plain Postgres). The Alembic
migrations in `apps/api/migrations/versions/` are the source of truth. The ORM models in
`app/models.py` only mirror them.

## Roles

| Role | Used by | Rights |
|---|---|---|
| `stllpay_owner` | Migrations only | Owns the schema |
| `stllpay_app` | The API and worker | Table rights granted per table. Subject to row-level security. Not the owner |

On Supabase, create both roles yourself. Don't run the app as `postgres` or `service_role`.

## Tables (migration `0001_foundation`)

| Table | Notes |
|---|---|
| `users` | Email (citext, unique), Argon2id hash, encrypted TOTP seed, last TOTP step (replay guard), lockout counters, `is_platform_admin` (owner-only), soft delete |
| `user_sessions` | SHA-256 of the token, idle and absolute expiry, `mfa_verified_at`, revocation |
| `user_tokens` | Email verification (later password reset). Hashed and single-use |
| `merchants` | Slug (unique, pattern-checked), names, NZBN (`94` + 11 digits), `status` (`draft`, `pending_review`, `restricted`, `active`, `suspended`), `live_enabled` (only when active) |
| `merchant_memberships` | (merchant, user) unique, role `owner`, `manager`, `cashier` or `viewer`. A trigger keeps at least one owner |
| `audit_logs` | Append-only, hash-chained (`prev_hash`, `hash`). `audit_verify_chain()` returns the first broken row |
| `audit_chain_head` | One row holding the last hash. The app role can't read it |

Payment tables (`payment_requests`, `payment_attempts`, provider events, QR codes, links,
POS integrations, API keys, webhooks) arrive in Phase 4 migrations, as designed in
[PHASE1_REPORT.md §6](PHASE1_REPORT.md#6-database).

## Row-level security

Each request sets transaction-local context with `set_config(..., true)`:
`app.user_id`, `app.merchant_id` and `app.is_platform_admin`. Policies read these through
`app_current_user_id()`, `app_current_merchant_id()` and `app_is_platform_admin()`. The
context is cleared when the transaction ends, so a pooled connection can't leak one
request's tenant into another.

- `merchants`: readable by members, by the current merchant context, or by admins. Writable only in its own merchant context.
- `merchant_memberships`: readable in the merchant's context or as your own rows. Writable only in the merchant's context.
- `audit_logs`: readable for the current merchant or by admins. Insert only.

## Migrations

```bash
cd apps/api
uv run alembic upgrade head      # apply
uv run alembic downgrade -1      # roll back one
uv run alembic revision -m "..."  # new migration (write SQL by hand; no autogenerate)
```

`MIGRATION_DATABASE_URL` must use the owner role. The test suite applies every migration,
rolls back to the base, and applies them again, so each downgrade is exercised.

## Money

Amounts are always `bigint` cents with a positive `CHECK`. There are no floats and no
wallet, balance or payout tables.
