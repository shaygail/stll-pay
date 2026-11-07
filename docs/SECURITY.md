# Security

STLL PAY is treated as financial software. This file describes the controls that exist
**now** (Phase 3) and lists what's still to do. It is not a certification. An independent
review is planned for Phase 8.

## Controls in place

| Area | Control | Where |
|---|---|---|
| Passwords | Argon2id, at least 12 characters with 3 character types, common-password and email checks. Unknown emails take the same time as wrong passwords | `app/core/security.py` |
| MFA | TOTP required for every login (no password-only access). Secrets encrypted with Fernet. Codes allowed ±1 step and never accepted twice | `app/auth/routes.py` |
| MFA freshness | Admins need MFA within 2 hours (Akahu requirement). Team changes need MFA within 10 minutes (step-up) | `app/auth/deps.py` |
| Sessions | Random 256-bit token in an `HttpOnly`, `SameSite=Lax` cookie (`Secure` when deployed). Stored only as SHA-256. 30-minute idle and 12-hour absolute expiry. Replaced after MFA. Revoked server-side on logout | `app/auth` |
| CSRF | Double-submit token header on every unsafe cookie-authenticated request, plus an `Origin` check (also on login and sign-up) | `app/auth/deps.py` |
| Brute force | Account lock after 5 failures for 15 minutes (in the database). Per-IP and per-email rate limits on sign-up, login, verification and MFA | `app/core/ratelimit.py` |
| Enumeration | Sign-up answers the same whether or not the email exists. Other tenants' records return 404, not 403 | |
| Tenant isolation | Merchant scoping in every route, **plus** Postgres row-level security (`FORCE`d) keyed on transaction-local settings. The app connects as a non-owner role | migration `0001` |
| Privilege guards in the database | Triggers stop the app role from making platform admins, approving merchants, enabling live payments, or removing the last owner | migration `0001` |
| Audit | Append-only (`INSERT` and `SELECT` only; triggers block `UPDATE`, `DELETE` and `TRUNCATE`). SHA-256 hash chain with a verification function. Sensitive keys are redacted before writing | `app/core/audit.py` |
| Input | Pydantic models reject unknown fields (status, live and slug can't be smuggled into updates). Amounts will be integer cents only | |
| Headers | API: `nosniff`, `DENY` framing, CSP `default-src 'none'`, `no-store`, HSTS when deployed. Web: CSP, `DENY` framing, `nosniff`, HSTS in production | `app/main.py`, `next.config.ts` |
| Secrets | Server-side only. Staging and production refuse development keys and credentials. Akahu credentials are never sent to the browser. `.env` files are git-ignored. CI runs a secret scan | `app/core/config.py` |
| Live payments | Off by default. Need `ENVIRONMENT=production`, `PAYMENTS_PROVIDER=akahu`, Akahu credentials, `LIVE_PAYMENTS_ENABLED=true`, **and** a per-merchant switch only a platform admin can flip on an active merchant | |
| Payments | Amounts are integer cents, validated on the server with strict types. Destinations come only from a verified, encrypted bank-account snapshot; the client can't supply one. Snapshots and amounts are immutable in the database. Webhooks are RSA-verified, deduplicated and re-confirmed with the provider. Indeterminate results are never retried automatically | `app/payments` |
| POS API | API keys are stored as hashes, shown once, and revocable. The integration on/off switch blocks keys immediately. Idempotency keys are required. Per-key rate limit. Outbound webhooks are HMAC-SHA256 signed with a timestamp. Webhook URLs must be https and public when deployed | `routes_pos.py`, `worker.py` |
| Shared devices | Logout does a full page reload, so no data from the session stays in browser memory (relevant for POS tablets) | `apps/web/lib/useMe.ts` |
| Dependencies | `pip-audit` and `npm audit --audit-level=high` in CI | `.github/workflows/ci.yml` |

## Known gaps and next steps

1. **Rate limits are kept in process memory.** They're per API instance; move them to Postgres or Redis before running more than one instance. Account lockout is already shared.
2. **Client IP behind proxies.** When deployed behind Vercel or a load balancer, run uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy ranges>` so rate limits see the real client IP.
3. **Web CSP** allows `'unsafe-inline'` scripts. Phase 8 moves to per-request nonces.
4. **No email provider yet.** Deployed environments refuse to start sending until one is configured.
5. **No password reset or MFA recovery codes yet** (Phase 6). Recovery must be multi-factor, per Akahu's rules.
6. **Passkeys** are planned after the MVP.
7. **Platform admins** are created by the schema owner with SQL. This is manual and intentional, and should be done with two people.
8. **Webhook URL checks** don't yet resolve DNS, so a public name pointing at a private IP (DNS rebinding) isn't blocked. Phase 8 adds an egress allowlist or resolve-then-connect checks.
9. **Row-level security context is set by the API itself.** It protects against query bugs, not against a compromised API process. That's why secrets, the database owner role and admin creation are kept separate.
10. 8 moderate `npm audit` findings are in dev-only lint tooling (`minimatch` under ESLint). There are no high or critical findings.

## Reporting a vulnerability

Email the STLL HAUS owner directly (address to be published before launch). Don't open a
public GitHub issue.
