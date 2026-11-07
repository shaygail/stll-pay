"""State machine, Akahu status mapping, and database-level protections for payments."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.payments.providers.akahu_status import normalise_akahu
from app.payments.providers.mock import scenario_cents
from app.payments.states import ALLOWED, FINAL, Decision, Status, decide

from .pay import pos_request


@pytest.mark.parametrize("status,code,expected", [
    ("READY", None, Status.AWAITING_AUTHORISATION),
    ("AUTHORISED", None, Status.AUTHORISED),
    ("SUBMITTED", "PENDING", Status.PROCESSING),
    ("SUBMITTED", "SETTLEMENT_IN_PROGRESS", Status.PROCESSING),
    ("SENT", None, Status.SENT),
    ("CANCELLED", "TIMED_OUT", Status.EXPIRED),
    ("CANCELLED", "USER_CANCELLED", Status.CANCELLED),
    ("CANCELLED", "APP_CANCELLED", Status.CANCELLED),
    ("FAILED", "REJECTED", Status.FAILED),
    ("FAILED", "BANK_ERROR", Status.FAILED),
    ("FAILED", "INTERNAL_ERROR", Status.FAILED),
    ("FAILED", "BANK_ERROR_INDETERMINATE", Status.REQUIRES_REVIEW),
    ("FAILED", "SOMETHING_NEW", Status.REQUIRES_REVIEW),
    ("PAID", None, Status.REQUIRES_REVIEW),  # undocumented: never guessed
])
def test_akahu_mapping(status, code, expected):
    assert normalise_akahu(status, code) == expected


def test_final_states_never_change():
    for s in FINAL:
        assert ALLOWED[s] == frozenset()
        for t in Status:
            if t != s:
                assert decide(s, t) != Decision.APPLY


def test_approval_cannot_be_undone_by_cancellation_or_expiry():
    for s in (Status.AUTHORISED, Status.PROCESSING, Status.SENT):
        assert decide(s, Status.CANCELLED) == Decision.CONFLICT
        assert decide(s, Status.EXPIRED) == Decision.CONFLICT


def test_late_pending_states_are_stale_not_regressions():
    assert decide(Status.SENT, Status.PROCESSING) == Decision.STALE
    assert decide(Status.PROCESSING, Status.AWAITING_AUTHORISATION) == Decision.STALE
    assert decide(Status.FAILED, Status.AUTHORISED) == Decision.STALE
    assert decide(Status.SENT, Status.SENT) == Decision.SAME


def test_success_after_failure_is_a_conflict():
    assert decide(Status.FAILED, Status.SENT) == Decision.CONFLICT


def test_review_resolves_only_to_outcomes():
    assert decide(Status.REQUIRES_REVIEW, Status.SENT) == Decision.APPLY
    assert decide(Status.REQUIRES_REVIEW, Status.PROCESSING) == Decision.STALE


def test_demo_bank_amount_alias():
    assert scenario_cents(10515) == 515
    assert scenario_cents(515) == 515
    assert scenario_cents(10000) == 10000


# ---------------------------------------------------------------- database protections


@pytest.fixture
async def app_engine(owner_engine):
    engine = create_async_engine(get_settings().database_url)
    yield engine
    await engine.dispose()


async def test_rls_hides_other_merchants_payments(app, clock, owner_engine, shop, app_engine):
    from .pay import setup_shop
    other = await setup_shop(app, clock, owner_engine)
    req = await pos_request(shop, shop.network, 1000)
    async with app_engine.begin() as conn:
        await conn.execute(text("SELECT set_config('app.merchant_id', :m, true)"),
                           {"m": other.mid})
        for table in ("payment_requests", "merchant_bank_accounts", "api_keys",
                      "webhook_endpoints", "pos_integrations"):
            ids = {str(r[0]) for r in await conn.execute(
                text(f"SELECT merchant_id FROM {table}"))}
            assert ids <= {other.mid}, table
        hit = (await conn.execute(text("SELECT count(*) FROM payment_requests WHERE id = :id"),
                                  {"id": req["id"]})).scalar()
        assert hit == 0


async def test_request_amount_is_immutable(shop, app_engine):
    req = await pos_request(shop, shop.network, 1000)
    with pytest.raises(Exception, match="immutable"):
        async with app_engine.begin() as conn:
            await conn.execute(text("SELECT set_config('app.merchant_id', :m, true)"),
                               {"m": shop.mid})
            await conn.execute(text("UPDATE payment_requests SET amount_cents = 1 WHERE id = :id"),
                               {"id": req["id"]})


async def test_amounts_must_be_positive_cents(shop, app_engine):
    with pytest.raises(Exception, match="check"):
        async with app_engine.begin() as conn:
            await conn.execute(text("SELECT set_config('app.merchant_id', :m, true)"),
                               {"m": shop.mid})
            await conn.execute(text(
                "INSERT INTO payment_requests (id, merchant_id, checkout_token, source,"
                " amount_cents, reference, expires_at) VALUES (:id, :m, :t, 'dashboard', -5,"
                " 'SPAAAAAAAA', now())"),
                {"id": uuid.uuid4(), "m": shop.mid, "t": "x" * 30})


async def test_lookup_functions_return_ids_only(shop, app_engine):
    async with app_engine.begin() as conn:
        result = (await conn.execute(text("SELECT lookup_merchant_by_slug(:s)"),
                                     {"s": shop.merchant["slug"]})).scalar()
        assert str(result) == shop.mid
        # The caller's context is unchanged afterwards.
        flag = (await conn.execute(text("SELECT app_is_platform_admin()"))).scalar()
        assert flag is False
    with pytest.raises(Exception, match="permission denied"):
        async with app_engine.begin() as conn:
            await conn.execute(text("SELECT app_with_system_context(true)"))
