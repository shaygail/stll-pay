# STLL PAY

QR-based open banking payments for New Zealand businesses, from STLL HAUS.

> **Status: Phase 5 (Akahu integration) in progress. Demo Bank runs are waiting for sandbox credentials.**
> Real-money payments are **not** enabled. They stay disabled until Akahu approval, a
> security review and the full test milestones are complete.

## What it is

A customer scans a merchant's QR code, checks the merchant and the amount, approves the
payment in their own NZ bank through regulated open banking (Akahu), and sees a verified
status. The merchant gets payment updates automatically.

- Multi-merchant from day one, with strict tenant isolation (app checks plus Postgres row-level security)
- Non-custodial: no wallet and no pooled funds
- Never collects banking credentials
- "Paid" only from verified provider status, never from a customer redirect

## Repository layout

```
apps/
  api/          FastAPI, SQLAlchemy 2, Alembic, Postgres (Python 3.13, uv)
  web/          Next.js 16, React 19, Tailwind 4 (STLL REWARDS design tokens)
design/         Phase 2 design canvas source
docs/           Architecture, database, security, testing, Akahu findings
infra/          Local Postgres roles and databases
docker-compose.yml
```

## Quick start (local, mock payments)

```bash
docker compose up -d postgres
```

```bash
cd apps/api && cp .env.example .env && uv sync && uv run alembic upgrade head
```

```bash
cd apps/api && uv run uvicorn app.main:app --reload --port 8000
```

```bash
cd apps/api && uv run python -m app.worker
```

```bash
cd apps/web && cp .env.example .env.local && npm ci && npm run dev
```

Open http://localhost:3000. In development, verification emails are printed in the API
log (`EMAIL_BACKEND=console`).

To make a platform admin, run this as the schema owner. It's deliberately not possible
through the app.

```bash
psql postgresql://stllpay_owner:devowner@localhost:5432/stllpay_dev -c "UPDATE users SET is_platform_admin = true WHERE email = 'you@example.com'"
```

## Tests

See [docs/TESTING.md](docs/TESTING.md). In short:

```bash
cd apps/api && uv run pytest
```

```bash
cd apps/web && npm test && npm run e2e -- --trace off
```

## Documentation

| Doc | Contents |
|---|---|
| [PHASE1_REPORT.md](docs/PHASE1_REPORT.md) | Audit, architecture, roadmap, risks (approved) |
| [AKAHU_INTEGRATION.md](docs/AKAHU_INTEGRATION.md) | Verified Akahu facts and open questions |
| [DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md) | Tokens, status language, accessibility (approved) |
| [DATABASE.md](docs/DATABASE.md) | Schema, roles, row-level security, migrations |
| [SECURITY.md](docs/SECURITY.md) | Controls in place and known gaps |
| [TESTING.md](docs/TESTING.md) | How to run each suite and what it proves |
| [PAYMENT_LIFECYCLE.md](docs/PAYMENT_LIFECYCLE.md) | Requests, attempts, statuses, failure handling, worker |
| [POS_INTEGRATION.md](docs/POS_INTEGRATION.md) | POS API, idempotency, signed webhooks, success policy |

## Delivery phases

1. Research and audit ✓
2. UX/UI design ✓
3. Application foundation ✓
4. Payment engine (mock provider) ✓
5. Akahu integration (Demo Bank only) ← **current**
6. Merchant platform: single-merchant MVP for STLL HAUS (see PHASE1_REPORT §8a)
7. STLL POS integration
8. Security and live readiness

Each phase ends with a review and approval checkpoint before the next one starts.

## Security

Never commit secrets. Akahu credentials (`AKAHU_APP_TOKEN`, `AKAHU_APP_SECRET`) are
server-side only. They must never appear in client code, `NEXT_PUBLIC_*` variables, logs or
commits. See [docs/SECURITY.md](docs/SECURITY.md).
