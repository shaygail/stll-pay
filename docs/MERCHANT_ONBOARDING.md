# Merchants: launch mode and onboarding

## Launch mode (now): STLL HAUS only

Approved requirement (PHASE1_REPORT §8a): the first release has **one merchant, STLL HAUS**,
with one administrator and one verified **ANZ business** receiving account. The database,
tenant isolation and roles already support many merchants; the multi-merchant parts of the
interface are switched off.

| | Launch mode (`multi_merchant_enabled = false`) |
|---|---|
| Public sign-up | Closed. The API returns `403 signup_closed`; the page explains it |
| Creating businesses | Only platform admins (`403 merchant_creation_closed` for others) |
| Staff accounts | By **invitation** from the STLL HAUS owner |
| Dashboard | Login opens STLL HAUS directly; no business list, no "add a business" |
| Payments | Mock provider now; Akahu Demo Bank next. **Real payments off** |

The flag lives in the database (`platform_settings`). A database trigger stops anyone but
a platform admin from changing it.

## Setting up STLL HAUS (once per environment)

Run by an operator with the schema-owner database URL (`MIGRATION_DATABASE_URL`):

```bash
cd apps/api && uv run alembic upgrade head
```

```bash
cd apps/api && uv run python scripts/bootstrap_stll_haus.py --admin-email you@stllhaus.co --account-number 01-XXXX-XXXXXXX-XX --account-name "NAME ON THE ACCOUNT" --verified-by-evidence "How you checked ownership, e.g. ANZ statement dated ..."
```

- It creates the **administrator**: STLL HAUS owner plus platform admin. The output includes a
  one-time **invitation link**, valid 7 days; the same link is emailed. Open it, set a
  password, log in, and set up the authenticator app.
- It creates **STLL HAUS** (slug `stll-haus`), approved for test payments (`restricted`).
- It adds the **ANZ receiving account**. Only bank codes 01 and 06 are accepted. The account
  is marked verified only when you give `--verified-by-evidence`, and that text is stored in
  the audit log.
- It's safe to run again: nothing is duplicated. Re-running with evidence verifies a pending
  account.
- In production it also needs `--confirm-production`.

The counter QR then lives at `/pay/stll-haus`.

## Adding staff

On the STLL HAUS dashboard, the owner opens **Team**, enters an email and role (manager,
cashier or viewer), and selects **Invite**. This needs an authenticator code from the last
10 minutes.

The person gets an email link to set their password. They set up MFA at first login and
land in STLL HAUS with that role. Cashiers can take payments and create links; they can't
manage the team, bank account or integrations.

## Opening STLL PAY to more businesses (later)

When STLL HAUS has tested the platform, a platform admin goes to **Admin → Platform →
Additional merchants → Turn on**. This needs MFA from the last 2 hours and a written
reason; the change is audited. Then:

1. Public sign-up and "Add a business" reappear.
2. New businesses start as `draft`, submit for review, and are approved by an admin
   (`pending_review` → `restricted` for test payments → `active`).
3. Each business needs a verified receiving account before taking payments. Live payments
   need a separate per-merchant switch that only admins can flip, on active merchants, with
   live payments globally enabled.

No rebuild or data migration is needed. Turning the flag off again closes sign-up; existing
merchants keep working.

**Before turning this on**, confirm Akahu allows payments to third-party merchants
(AKAHU_INTEGRATION §6, question 1) and get the agreements and advice listed in
PHASE1_REPORT §10.
