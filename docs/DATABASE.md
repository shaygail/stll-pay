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

### Migration `0002_payments`

| Table | Notes |
|---|---|
| `merchant_bank_accounts` | Encrypted account number plus last 4 digits. Details are immutable after creation (trigger). Only a platform admin can verify. One verified account per merchant; verifying a new one retires the old |
| `pos_integrations` | Per-merchant integration with an `enabled` feature flag |
| `api_keys` | Prefix plus SHA-256 hash only. Revocable, optional expiry |
| `webhook_endpoints` | Merchant webhook URL with an encrypted signing secret |
| `static_qr_codes`, `payment_links` | Counter QR codes; fixed or open-amount links with expiry and use limits |
| `payment_requests` | Integer cents, NZD only, unique `SP` reference, random checkout token. Unique per idempotency key, and at most one live request per POS order. Amount, merchant and reference are immutable |
| `payment_attempts` | Immutable destination snapshot and amount. At most one in flight and one successful per request (partial unique indexes). Final statuses can't change |
| `payment_provider_events` | Every verified webhook, deduplicated on a unique hash key |
| `payment_reconciliations` | Every status change, with method (webhook, poll, admin and so on) |
| `payment_events` | At most one outcome event per request and type |
| `webhook_deliveries` | Outbox for POS webhooks with retry state |
| `mock_provider.*` | The mock provider's own state (development and tests; unused in production) |

All merchant-owned payment tables have row-level security. Public checkout endpoints use
narrow `SECURITY DEFINER` lookups (`lookup_merchant_by_slug`, `lookup_request_by_token`,
and so on). Each returns only a merchant id, and the caller then works inside that merchant's
context.

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
