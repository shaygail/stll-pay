"""Create an approved test merchant with a verified bank account, for end-to-end tests.

Prints JSON with the owner's login details and TOTP secret. Refuses to run outside the
test environment or against a database whose name lacks "test" or "e2e".
"""

from __future__ import annotations

import json
import secrets
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.ids import uuid7  # noqa: E402
from app.core.security import encrypt_field, hash_password, new_totp_secret  # noqa: E402

PASSWORD = "Correct-Horse-42-battery"  # noqa: S105 - test-only, disposable e2e database


def main() -> None:
    settings = get_settings()
    name = make_url(settings.migration_database_url).database or ""
    if settings.environment not in ("test", "development") or not any(
            word in name for word in ("test", "e2e", "dev")):
        raise SystemExit(f"Refusing to seed {name!r} in {settings.environment!r}")
    suffix = secrets.token_hex(4)
    email = f"e2e-owner-{suffix}@example.com"
    slug = f"e2e-shop-{suffix}"
    secret = new_totp_secret()
    user_id, merchant_id, account_id = uuid7(), uuid7(), uuid7()
    now = datetime.now(UTC)
    engine = create_engine(settings.migration_database_url)
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.is_platform_admin', 'true', true)"))
        conn.execute(text(
            "INSERT INTO users (id, email, password_hash, email_verified_at, mfa_secret_enc,"
            " mfa_enabled_at) VALUES (:id, :e, :p, :now, :s, :now)"),
            {"id": user_id, "e": email, "p": hash_password(PASSWORD), "now": now,
             "s": encrypt_field(secret)})
        conn.execute(text("SELECT set_config('app.user_id', :u, true),"
                          " set_config('app.merchant_id', :m, true)"),
                     {"u": str(user_id), "m": str(merchant_id)})
        conn.execute(text(
            "INSERT INTO merchants (id, slug, legal_name, trading_name, status) VALUES"
            " (:id, :slug, 'E2E Shop Limited', 'E2E Shop', 'restricted')"),
            {"id": merchant_id, "slug": slug})
        conn.execute(text("INSERT INTO merchant_memberships (id, merchant_id, user_id, role)"
                          " VALUES (:id, :m, :u, 'owner')"),
                     {"id": uuid.uuid4(), "m": merchant_id, "u": user_id})
        conn.execute(text(
            "INSERT INTO merchant_bank_accounts (id, merchant_id, account_number_enc,"
            " account_last4, holder_name, status, verified_at) VALUES (:id, :m, :acct, '2300',"
            " 'E2E Shop', 'verified', :now)"),
            {"id": account_id, "m": merchant_id, "acct": encrypt_field("12-3456-7890123-00"),
             "now": now})
    engine.dispose()
    print(json.dumps({"email": email, "password": PASSWORD, "totp_secret": secret,
                      "slug": slug, "merchant_id": str(merchant_id)}))


if __name__ == "__main__":
    main()
