"""Launch mode: STLL HAUS only. Sign-up closed, staff by invitation, admin feature flag."""

from __future__ import annotations

import re

import pyotp
import pytest
from sqlalchemy import text

from .conftest import (
    PASSWORD,
    csrf,
    latest_email_to,
    make_admin,
    make_user,
    new_client,
    set_multi_merchant,
    unique_email,
)


@pytest.fixture
def launch_mode(owner_engine):
    set_multi_merchant(owner_engine, False)
    yield
    set_multi_merchant(owner_engine, True)


async def test_public_signup_closed(client, launch_mode):
    r = await client.post("/v1/auth/signup", json={"email": unique_email(), "password": PASSWORD})
    assert r.status_code == 403 and r.json()["error"]["code"] == "signup_closed"
    assert (await client.get("/v1/config")).json()["multi_merchant_enabled"] is False


async def test_merchants_cannot_create_businesses_in_launch_mode(app, clock, owner_engine):
    user = await make_user(app, clock)
    set_multi_merchant(owner_engine, False)
    try:
        r = await user.post("/v1/merchants", json={
            "slug": "sneaky-shop", "legal_name": "X", "trading_name": "X"})
        assert r.status_code == 403 and r.json()["error"]["code"] == "merchant_creation_closed"
    finally:
        set_multi_merchant(owner_engine, True)


async def test_admin_flag_needs_platform_admin_and_is_audited(app, clock, owner_engine):
    user = await make_user(app, clock)
    r = await user.patch("/v1/admin/platform-settings",
                         json={"multi_merchant_enabled": False, "reason": "try"})
    assert r.status_code == 404
    admin = await make_user(app, clock, "admin")
    make_admin(owner_engine, admin)
    r = await admin.patch("/v1/admin/platform-settings",
                          json={"multi_merchant_enabled": False, "reason": "launch: STLL HAUS"})
    assert r.status_code == 200 and r.json()["multi_merchant_enabled"] is False
    assert (await new_client(app).get("/v1/config")).json()["multi_merchant_enabled"] is False
    r = await admin.patch("/v1/admin/platform-settings",
                          json={"multi_merchant_enabled": True, "reason": "open to more"})
    assert r.json()["multi_merchant_enabled"] is True
    with owner_engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.is_platform_admin', 'true', false)"))
        n = conn.execute(text("SELECT count(*) FROM audit_logs"
                              " WHERE action = 'admin.platform_settings'")).scalar()
    assert n >= 2


async def test_database_blocks_flag_change_by_non_admin(owner_engine):
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings
    engine = create_async_engine(get_settings().database_url)
    try:
        with pytest.raises(Exception, match="platform admin"):
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE platform_settings"
                                        " SET multi_merchant_enabled = true"))
    finally:
        await engine.dispose()


async def test_staff_join_by_invitation(app, clock, owner_engine, launch_mode):
    from .pay import setup_shop
    set_multi_merchant(owner_engine, True)  # setup uses sign-up for the owner
    shop = await setup_shop(app, clock, owner_engine)
    set_multi_merchant(owner_engine, False)
    email = unique_email("cashier")
    r = await shop.owner.post(f"/v1/merchants/{shop.mid}/members",
                              json={"email": email, "role": "cashier"})
    assert r.status_code == 201 and r.json()["invite_pending"] is True
    token = re.search(r"token=([\w-]+)", latest_email_to(email)["body"]).group(1)

    staff = new_client(app)
    # Can't log in before accepting (no password yet).
    r = await staff.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code in (401, 403)
    r = await staff.post("/v1/auth/accept-invite", json={"token": token, "password": "short"})
    assert r.status_code == 422
    r = await staff.post("/v1/auth/accept-invite", json={"token": token, "password": PASSWORD})
    assert r.status_code == 200
    # Single use.
    r = await staff.post("/v1/auth/accept-invite", json={"token": token, "password": PASSWORD})
    assert r.status_code == 400
    # First login requires MFA setup, then they land in STLL HAUS as a cashier.
    r = await staff.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.json() == {"mfa_required": True, "mfa_enrolled": False}
    secret = (await staff.post("/v1/auth/mfa/setup", headers=csrf(staff))).json()["secret"]
    clock.tick()
    await staff.post("/v1/auth/mfa/enable", headers=csrf(staff),
                     json={"code": pyotp.TOTP(secret).at(clock.now)})
    me = (await staff.get("/v1/me")).json()
    assert [(m["merchant_id"], m["role"]) for m in me["memberships"]] == [(shop.mid, "cashier")]


# ---------------------------------------------------------------- bootstrap script


def _bootstrap(*extra):
    from scripts.bootstrap_stll_haus import main
    return main(["--admin-email", "owner@stllhaus.example.com",
                 "--account-number", "01-0123-0123456-00",
                 "--account-name", "STLL HAUS LIMITED", *extra])


async def test_bootstrap_creates_stll_haus_once(app, owner_engine):
    first = _bootstrap()
    assert first["merchant"] == "created" and first["admin"] == "created"
    assert first["bank_account_status"] == "pending"
    assert "/accept-invite?token=" in first["invite_link"]
    second = _bootstrap("--verified-by-evidence", "ANZ statement Oct 2026, name matches")
    assert second["merchant"].startswith("existing") and second["admin"] == "existing"
    assert second["merchant_id"] == first["merchant_id"]
    assert second["bank_account_status"] == "verified"
    with owner_engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.is_platform_admin', 'true', false)"))
        counts = conn.execute(text(
            "SELECT (SELECT count(*) FROM merchants WHERE slug = 'stll-haus'),"
            " (SELECT count(*) FROM merchant_bank_accounts b JOIN merchants m"
            "  ON m.id = b.merchant_id WHERE m.slug = 'stll-haus'),"
            " (SELECT count(*) FROM merchant_memberships mm JOIN merchants m"
            "  ON m.id = mm.merchant_id WHERE m.slug = 'stll-haus'),"
            " (SELECT is_platform_admin FROM users"
            "  WHERE email = 'owner@stllhaus.example.com')")).one()
        evidence = conn.execute(text(
            "SELECT metadata->>'evidence' FROM audit_logs"
            " WHERE action = 'bootstrap.bank_account_verified'")).scalar()
    assert tuple(counts) == (1, 1, 1, True)
    assert "ANZ statement" in evidence
    # STLL HAUS can take test payments straight away.
    page = (await new_client(app).get("/v1/checkout/merchants/stll-haus")).json()
    assert page["trading_name"] == "STLL HAUS" and page["accepting_payments"] is True


def test_bootstrap_refuses_non_anz_accounts():
    from scripts.bootstrap_stll_haus import main
    with pytest.raises(SystemExit, match="ANZ"):
        main(["--admin-email", "x@stllhaus.example.com", "--account-number",
              "12-3456-7890123-00", "--account-name", "Not ANZ"])
