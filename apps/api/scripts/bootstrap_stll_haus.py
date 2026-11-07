"""Set up the launch merchant: STLL HAUS, its single administrator, and its ANZ account.

Run once per environment by an operator with schema-owner database access. Safe to run
again: nothing is duplicated.

    uv run python scripts/bootstrap_stll_haus.py \\
        --admin-email you@stllhaus.co \\
        --account-number 01-0123-0123456-00 --account-name "STLL HAUS LIMITED" \\
        --verified-by-evidence "ANZ business statement for Oct 2026, account name matches"

What it does:
1. Creates (or finds) the administrator: owner of STLL HAUS and platform admin. A new
   administrator gets a one-time invitation link to set a password; MFA is set up at first
   login.
2. Creates (or finds) the STLL HAUS merchant, approved for test payments ("restricted").
   Live payments stay off; that needs Akahu approval and the live switches.
3. Adds the ANZ business receiving account. It is marked verified only when you pass
   --verified-by-evidence describing how ownership was checked (recorded in the audit log).
   Otherwise it stays pending.

Only ANZ accounts (bank code 01 or 06) are accepted for the launch merchant.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.email import send_email  # noqa: E402
from app.core.ids import uuid7  # noqa: E402
from app.core.security import decrypt_field, encrypt_field, new_token, token_hash  # noqa: E402
from app.merchants.routes import INVITE_DAYS, INVITED_PASSWORD_HASH  # noqa: E402
from app.payments.routes_merchant import normalise_account  # noqa: E402

SLUG = "stll-haus"
ANZ_BANK_CODES = {"01", "06"}


def _audit(conn: Connection, action: str, merchant_id: object, actor: object,
           meta: dict[str, object]) -> None:
    conn.execute(text(
        "INSERT INTO audit_logs (actor_user_id, merchant_id, action, metadata, prev_hash, hash)"
        " VALUES (:a, :m, :act, CAST(:meta AS jsonb), '\\x00', '\\x00')"),
        {"a": actor, "m": merchant_id, "act": action,
         "meta": json.dumps({**meta, "via": "bootstrap_stll_haus"})})


def run(args: argparse.Namespace) -> dict[str, object]:
    settings = get_settings()
    if settings.environment == "production" and not args.confirm_production:
        raise SystemExit("Production: re-run with --confirm-production.")
    account = normalise_account(args.account_number)
    if account.split("-")[0] not in ANZ_BANK_CODES:
        raise SystemExit(f"{account} isn't an ANZ account (bank code 01 or 06).")
    report: dict[str, object] = {"environment": settings.environment}
    engine = create_engine(settings.migration_database_url)
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.is_platform_admin', 'true', true)"))

        # 1. Administrator
        email = args.admin_email.strip().lower()
        admin = conn.execute(text("SELECT id, email_verified_at FROM users WHERE email = :e"),
                             {"e": email}).first()
        if admin is None:
            admin_id = uuid7()
            conn.execute(text("INSERT INTO users (id, email, password_hash, is_platform_admin)"
                              " VALUES (:id, :e, :p, true)"),
                         {"id": admin_id, "e": email, "p": INVITED_PASSWORD_HASH})
            verified = False
            report["admin"] = "created"
        else:
            admin_id, verified = admin[0], admin[1] is not None
            conn.execute(text("UPDATE users SET is_platform_admin = true WHERE id = :id"),
                         {"id": admin_id})
            report["admin"] = "existing"
        if not verified:
            token = new_token()
            conn.execute(text(
                "INSERT INTO user_tokens (id, user_id, purpose, token_hash, expires_at)"
                " VALUES (:id, :u, 'invite', :h, :exp)"),
                {"id": uuid7(), "u": admin_id, "h": token_hash(token),
                 "exp": datetime.now(UTC) + timedelta(days=INVITE_DAYS)})
            link = f"{settings.web_origin}/accept-invite?token={token}"
            send_email(email, "Set up your STLL PAY administrator account",
                       f"Set your password here (valid for {INVITE_DAYS} days): {link}")
            report["invite_link"] = link

        # 2. Merchant
        merchant = conn.execute(text("SELECT id, status FROM merchants WHERE slug = :s"),
                                {"s": SLUG}).first()
        if merchant is None:
            merchant_id = uuid7()
            conn.execute(text("SELECT set_config('app.user_id', :u, true),"
                              " set_config('app.merchant_id', :m, true)"),
                         {"u": str(admin_id), "m": str(merchant_id)})
            conn.execute(text(
                "INSERT INTO merchants (id, slug, legal_name, trading_name, nzbn, status)"
                " VALUES (:id, :s, :legal, :trading, :nzbn, 'restricted')"),
                {"id": merchant_id, "s": SLUG, "legal": args.legal_name,
                 "trading": args.trading_name, "nzbn": args.nzbn})
            _audit(conn, "bootstrap.merchant_created", merchant_id, admin_id, {"slug": SLUG})
            report["merchant"] = "created"
        else:
            merchant_id = merchant[0]
            conn.execute(text("SELECT set_config('app.merchant_id', :m, true)"),
                         {"m": str(merchant_id)})
            report["merchant"] = f"existing ({merchant[1]})"
        report["merchant_id"] = str(merchant_id)

        # 3. Owner membership
        conn.execute(text(
            "INSERT INTO merchant_memberships (id, merchant_id, user_id, role)"
            " VALUES (:id, :m, :u, 'owner') ON CONFLICT (merchant_id, user_id)"
            " DO UPDATE SET role = 'owner'"),
            {"id": uuid7(), "m": merchant_id, "u": admin_id})

        # 4. ANZ receiving account
        rows = conn.execute(text(
            "SELECT id, account_number_enc, status FROM merchant_bank_accounts"
            " WHERE merchant_id = :m AND status IN ('pending', 'verified')"),
            {"m": merchant_id}).all()
        existing = next((r for r in rows if decrypt_field(r[1]) == account), None)
        if existing is None:
            account_id = uuid7()
            conn.execute(text(
                "INSERT INTO merchant_bank_accounts (id, merchant_id, account_number_enc,"
                " account_last4, holder_name, created_by) VALUES (:id, :m, :enc, :l4, :n, :u)"),
                {"id": account_id, "m": merchant_id, "enc": encrypt_field(account),
                 "l4": account.replace("-", "")[-4:], "n": args.account_name, "u": admin_id})
            _audit(conn, "bootstrap.bank_account_added", merchant_id, admin_id,
                   {"last4": account.replace("-", "")[-4:]})
            status = "pending"
        else:
            account_id, status = existing[0], existing[2]
        if status != "verified" and args.verified_by_evidence:
            conn.execute(text(
                "UPDATE merchant_bank_accounts SET status = 'retired', retired_at = now()"
                " WHERE merchant_id = :m AND status = 'verified'"), {"m": merchant_id})
            conn.execute(text(
                "UPDATE merchant_bank_accounts SET status = 'verified', verified_by = :u,"
                " verified_at = now() WHERE id = :id"), {"u": admin_id, "id": account_id})
            _audit(conn, "bootstrap.bank_account_verified", merchant_id, admin_id,
                   {"evidence": args.verified_by_evidence})
            status = "verified"
        report["bank_account"] = f"{args.account_name} ··{account.replace('-', '')[-4:]}"
        report["bank_account_status"] = status
    engine.dispose()
    return report


def main(argv: list[str] | None = None) -> dict[str, object]:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--admin-email", required=True)
    p.add_argument("--account-number", required=True, help="ANZ business account")
    p.add_argument("--account-name", required=True, help="Name on the account")
    p.add_argument("--verified-by-evidence",
                   help="How ownership was checked; marks the account verified")
    p.add_argument("--trading-name", default="STLL HAUS")
    p.add_argument("--legal-name", default="STLL HAUS")
    p.add_argument("--nzbn", default=None)
    p.add_argument("--confirm-production", action="store_true")
    report = run(p.parse_args(argv))
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()
