"""Merchant isolation: through the API and directly against row-level security."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings

from .conftest import make_merchant, make_user


@pytest.fixture
async def two_tenants(app, clock):
    alice = await make_user(app, clock, "alice")
    bob = await make_user(app, clock, "bob")
    a = await make_merchant(alice)
    b = await make_merchant(bob)
    return alice, bob, a, b


async def test_owner_sees_own_merchant(two_tenants):
    alice, _, a, _ = two_tenants
    r = await alice.get(f"/v1/merchants/{a['id']}")
    assert r.status_code == 200
    assert r.json()["role"] == "owner"
    assert r.json()["status"] == "draft"
    assert r.json()["live_enabled"] is False
    me = (await alice.get("/v1/me")).json()
    assert [m["merchant_id"] for m in me["memberships"]] == [a["id"]]


@pytest.mark.parametrize("method,path", [
    ("get", ""), ("patch", ""), ("post", "/submit"), ("get", "/members"),
    ("post", "/members"), ("get", "/audit"),
])
async def test_other_merchant_is_invisible(two_tenants, method, path):
    _, bob, a, _ = two_tenants
    kwargs = {}
    if method == "patch":
        kwargs["json"] = {"trading_name": "Hijacked"}
    if path == "/members" and method == "post":
        kwargs["json"] = {"email": "x@example.com", "role": "viewer"}
    r = await getattr(bob, method)(f"/v1/merchants/{a['id']}{path}", **kwargs)
    # 404, not 403: another tenant's IDs can't be confirmed to exist.
    # (Adding members needs step-up first, which also refuses with 401.)
    assert r.status_code in (404, 401), r.text
    if r.status_code == 401:
        assert r.json()["error"]["code"] == "step_up_required"


async def test_random_merchant_id_is_404(two_tenants):
    alice, *_ = two_tenants
    assert (await alice.get(f"/v1/merchants/{uuid.uuid4()}")).status_code == 404


async def test_cannot_set_status_or_live_through_update(two_tenants):
    alice, _, a, _ = two_tenants
    for field, value in (("status", "active"), ("live_enabled", True), ("slug", "new-slug")):
        r = await alice.patch(f"/v1/merchants/{a['id']}", json={field: value})
        assert r.status_code == 422, field


async def test_duplicate_slug_rejected(two_tenants):
    alice, _, a, _ = two_tenants
    r = await alice.post("/v1/merchants", json={
        "slug": a["slug"], "legal_name": "Copy", "trading_name": "Copy"})
    assert r.status_code == 409


async def test_submit_for_review_once(two_tenants):
    alice, _, a, _ = two_tenants
    r = await alice.post(f"/v1/merchants/{a['id']}/submit")
    assert r.status_code == 200
    assert r.json()["status"] == "pending_review"
    assert (await alice.post(f"/v1/merchants/{a['id']}/submit")).status_code == 409


# ---------------------------------------------------------------- direct RLS checks


@pytest.fixture
async def app_conn(owner_engine):
    """A raw connection as the application role, outside the API."""
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as conn:
        yield conn
    await engine.dispose()


async def _ctx(conn, user_id=None, merchant_id=None, admin=False):
    for key, value in (("app.user_id", user_id), ("app.merchant_id", merchant_id),
                       ("app.is_platform_admin", "true" if admin else "false")):
        await conn.execute(text("SELECT set_config(:k, :v, true)"),
                           {"k": key, "v": str(value) if value else ""})


async def test_rls_without_context_returns_nothing(two_tenants, app_conn):
    async with app_conn.begin():
        rows = (await app_conn.execute(text("SELECT id FROM merchants"))).all()
        members = (await app_conn.execute(text("SELECT id FROM merchant_memberships"))).all()
    assert rows == [] and members == []


async def test_rls_scopes_rows_to_current_merchant(two_tenants, app_conn):
    _, bob, _a, b = two_tenants
    async with app_conn.begin():
        await _ctx(app_conn, user_id=bob.id, merchant_id=b["id"])
        ids = {str(r[0]) for r in (await app_conn.execute(text("SELECT id FROM merchants")))}
        audit_merchants = {str(r[0]) for r in await app_conn.execute(
            text("SELECT DISTINCT merchant_id FROM audit_logs WHERE merchant_id IS NOT NULL"))}
    assert ids == {b["id"]}
    assert audit_merchants <= {b["id"]}


async def test_rls_blocks_writes_to_other_merchant(two_tenants, app_conn):
    _alice, bob, a, b = two_tenants
    async with app_conn.begin():
        await _ctx(app_conn, user_id=bob.id, merchant_id=b["id"])
        result = await app_conn.execute(
            text("UPDATE merchants SET trading_name = 'pwned' WHERE id = :id"), {"id": a["id"]})
        assert result.rowcount == 0
    with pytest.raises(ProgrammingError):
        async with app_conn.begin():
            await _ctx(app_conn, user_id=bob.id, merchant_id=b["id"])
            # Bob tries to add himself to Alice's merchant.
            await app_conn.execute(text(
                "INSERT INTO merchant_memberships (id, merchant_id, user_id, role)"
                " VALUES (:id, :m, :u, 'owner')"),
                {"id": uuid.uuid4(), "m": a["id"], "u": bob.id})


async def test_database_blocks_self_approval(two_tenants, app_conn):
    alice, _, a, _ = two_tenants
    for sql in ("UPDATE merchants SET status = 'active' WHERE id = :id",
                "UPDATE merchants SET live_enabled = true WHERE id = :id"):
        with pytest.raises(DBAPIError):
            async with app_conn.begin():
                await _ctx(app_conn, user_id=alice.id, merchant_id=a["id"])
                await app_conn.execute(text(sql), {"id": a["id"]})


async def test_database_blocks_platform_admin_escalation(two_tenants, app_conn):
    alice, *_ = two_tenants
    with pytest.raises(DBAPIError):
        async with app_conn.begin():
            await app_conn.execute(
                text("UPDATE users SET is_platform_admin = true WHERE id = :id"), {"id": alice.id})


async def test_app_role_cannot_read_chain_head(app_conn, owner_engine):
    with pytest.raises(ProgrammingError):
        async with app_conn.begin():
            await app_conn.execute(text("SELECT * FROM audit_chain_head"))


async def test_only_admins_can_verify_audit_chain(app_conn, owner_engine):
    with pytest.raises(DBAPIError):
        async with app_conn.begin():
            await app_conn.execute(text("SELECT audit_verify_chain()"))
