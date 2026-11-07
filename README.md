# STLL PAY

QR-based open banking payments for New Zealand businesses, from STLL HAUS.

> **Status: Phase 1 (research and audit). No application code yet.**
> Real-money payments are **not** enabled and will stay disabled until provider approval,
> a security review and the full test milestones are done.

## What it is

STLL PAY lets a customer scan a merchant's QR code, check the merchant and the amount,
approve the payment in their own NZ bank through regulated open banking (Akahu), and
see a verified payment status. The merchant gets payment updates automatically.

- Multi-merchant from day one, with strict tenant isolation
- Non-custodial: no wallet and no pooled customer funds
- Never collects or stores banking credentials
- Payment success comes only from verified provider information, never from a customer redirect
- Standalone service with a versioned API for STLL POS and other integrations

## Planned stack

| Layer    | Choice                                              |
|----------|-----------------------------------------------------|
| Frontend | Next.js, TypeScript, Tailwind CSS, Geist and Satoshi |
| Backend  | FastAPI, SQLAlchemy, Alembic, Pydantic              |
| Database | PostgreSQL (amounts stored as integer NZD cents)    |
| Payments | Provider interface: `MockPaymentProvider`, `AkahuPaymentProvider` |
| Testing  | pytest, Playwright, mock provider and Akahu Demo Bank |

## Delivery phases

1. Research and audit ← **current**
2. UX/UI design
3. Application foundation
4. Payment engine (mock provider)
5. Akahu integration (Demo Bank only)
6. Merchant platform
7. STLL POS integration
8. Security and live readiness

Each phase ends with a review and approval checkpoint before the next one starts.

## Security

Never commit secrets. Akahu credentials (`AKAHU_APP_TOKEN`, `AKAHU_APP_SECRET`) are
server-side only and must not appear in client code, public env vars, logs or commits.
