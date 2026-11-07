"""Roles, step-up verification, team rules, platform admin and the audit trail."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from .conftest import code_for, make_admin, make_merchant, make_user


async def _set_mfa_age(owner_engine, user, minutes: int) -> None:
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE user_sessions SET mfa_verified_at = :t WHERE user_id = :id"
                          " AND revoked_at IS NULL"),
                     {"t": datetime.now(UTC) - timedelta(minutes=minutes), "id": user.id})


@pytest.fixture
async def team(app, clock, owner_engine):
    owner = await make_user(app, clock, "owner")
    merchant = await make_merchant(owner)
    members = {}
    for role in ("manager", "cashier", "viewer"):
        u = await make_user(app, clock, role)
        r = await owner.post(f"/v1/merchants/{merchant['id']}/members",
                             json={"email": u.email, "role": role})
        assert r.status_code == 201, r.text
        members[role] = u
    return owner, merchant, members


async def test_team_changes_need_recent_step_up(app, clock, owner_engine):
    owner = await make_user(app, clock, "owner")
    merchant = await make_merchant(owner)
    other = await make_user(app, clock, "other")
    await _set_mfa_age(owner_engine, owner, 30)
    url = f"/v1/merchants/{merchant['id']}/members"
    r = await owner.post(url, json={"email": other.email, "role": "viewer"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "step_up_required"
    assert (await owner.post("/v1/auth/step-up", json={"code": code_for(owner, clock)})
            ).status_code == 200
    assert (await owner.post(url, json={"email": other.email, "role": "viewer"})
            ).status_code == 201


@pytest.mark.parametrize("role,can_update,can_list_team", [
    ("manager", True, True), ("cashier", False, False), ("viewer", False, False),
])
async def test_role_permissions(team, role, can_update, can_list_team):
    _, merchant, members = team
    user = members[role]
    base = f"/v1/merchants/{merchant['id']}"
    assert (await user.get(base)).status_code == 200
    r = await user.patch(base, json={"trading_name": f"Renamed by {role}"})
    assert r.status_code == (200 if can_update else 403)
    assert (await user.get(f"{base}/members")).status_code == (200 if can_list_team else 403)
    # Only owners manage the team.
    r = await user.post(f"{base}/members", json={"email": "x@example.com", "role": "viewer"})
    assert r.status_code == 403


async def test_last_owner_cannot_leave_or_be_demoted(team):
    owner, merchant, _ = team
    base = f"/v1/merchants/{merchant['id']}/members"
    items = (await owner.get(base)).json()["items"]
    me = next(i for i in items if i["role"] == "owner")
    r = await owner.patch(f"{base}/{me['id']}", json={"role": "viewer"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "last_owner"
    assert (await owner.delete(f"{base}/{me['id']}")).status_code == 409


async def test_owner_can_change_and_remove_member(team):
    owner, merchant, members = team
    base = f"/v1/merchants/{merchant['id']}/members"
    items = (await owner.get(base)).json()["items"]
    cashier = next(i for i in items if i["role"] == "cashier")
    r = await owner.patch(f"{base}/{cashier['id']}", json={"role": "viewer"})
    assert r.status_code == 200 and r.json()["role"] == "viewer"
    assert (await owner.delete(f"{base}/{cashier['id']}")).status_code == 200
    assert (await members["cashier"].get(f"/v1/merchants/{merchant['id']}")).status_code == 404


async def test_adding_someone_without_an_account_invites_them(team):
    from .conftest import latest_email_to, unique_email
    owner, merchant, _ = team
    email = unique_email("newstaff")
    r = await owner.post(f"/v1/merchants/{merchant['id']}/members",
                         json={"email": email, "role": "cashier"})
    assert r.status_code == 201 and r.json()["invite_pending"] is True
    assert "/accept-invite?token=" in latest_email_to(email)["body"]


async def test_merchant_audit_records_team_changes(team):
    owner, merchant, _ = team
    items = (await owner.get(f"/v1/merchants/{merchant['id']}/audit")).json()["items"]
    actions = [i["action"] for i in items]
    assert "merchant.created" in actions
    assert actions.count("team.member_added") == 3


# ---------------------------------------------------------------- platform admin


@pytest.fixture
async def admin(app, clock, owner_engine):
    user = await make_user(app, clock, "admin")
    make_admin(owner_engine, user)
    return user


async def test_admin_area_hidden_from_merchants(app, clock):
    user = await make_user(app, clock)
    assert (await user.get("/v1/admin/merchants")).status_code == 404


async def test_admin_approval_flow(admin, app, clock):
    owner = await make_user(app, clock, "owner")
    merchant = await make_merchant(owner)
    url = f"/v1/admin/merchants/{merchant['id']}/status"
    r = await admin.post(url, json={"status": "active", "reason": "skip review"})
    assert r.status_code == 409  # draft can't jump to active
    await owner.post(f"/v1/merchants/{merchant['id']}/submit")
    pending = (await admin.get("/v1/admin/merchants?status=pending_review")).json()["items"]
    assert merchant["id"] in [m["id"] for m in pending]
    r = await admin.post(url, json={"status": "active", "reason": "documents checked"})
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert r.json()["live_enabled"] is False


async def test_live_payments_cannot_be_enabled_while_globally_off(admin, app, clock):
    owner = await make_user(app, clock, "owner")
    merchant = await make_merchant(owner)
    await owner.post(f"/v1/merchants/{merchant['id']}/submit")
    await admin.post(f"/v1/admin/merchants/{merchant['id']}/status",
                     json={"status": "active", "reason": "ok"})
    r = await admin.post(f"/v1/admin/merchants/{merchant['id']}/live",
                         json={"enabled": True, "reason": "go live"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "live_payments_off"


async def test_admin_needs_mfa_within_two_hours(admin, owner_engine):
    await _set_mfa_age(owner_engine, admin, 121)
    r = await admin.get("/v1/admin/merchants")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "step_up_required"


async def test_admin_has_read_only_access_to_merchant(admin, app, clock):
    owner = await make_user(app, clock, "owner")
    merchant = await make_merchant(owner)
    assert (await admin.get(f"/v1/merchants/{merchant['id']}")).status_code == 200
    r = await admin.patch(f"/v1/merchants/{merchant['id']}", json={"trading_name": "Admin edit"})
    assert r.status_code == 403


async def test_audit_chain_intact_and_tamper_evident(admin, owner_engine):
    r = await admin.get("/v1/admin/audit/verify")
    assert r.json() == {"intact": True, "first_broken_id": None}
    # Simulate an attacker with owner access editing history (rolled back afterwards).
    with owner_engine.connect() as conn:
        tx = conn.begin()
        conn.execute(text("ALTER TABLE audit_logs NO FORCE ROW LEVEL SECURITY"))
        conn.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER audit_logs_no_update"))
        target = conn.execute(text("SELECT min(id) FROM audit_logs")).scalar()
        conn.execute(text("UPDATE audit_logs SET action = 'edited' WHERE id = :id"),
                     {"id": target})
        conn.execute(text("SELECT set_config('app.is_platform_admin', 'true', true)"))
        assert conn.execute(text("SELECT audit_verify_chain()")).scalar() == target
        tx.rollback()


async def test_audit_log_is_append_only_for_app_role(admin):
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        for sql in ("UPDATE audit_logs SET action = 'x'", "DELETE FROM audit_logs"):
            with pytest.raises(DBAPIError):
                async with engine.begin() as conn:
                    await conn.execute(
                        text("SELECT set_config('app.is_platform_admin', 'true', true)"))
                    await conn.execute(text(sql))
    finally:
        await engine.dispose()
