"""The failure and abuse scenarios from the brief (section 12), against the mock provider.

Magic amounts follow Akahu Demo Bank (see app/payments/providers/mock.py).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import text

from app.payments.providers.base import ErrorKind, ProviderError
from app.payments.providers.registry import get_provider

from .conftest import code_for, make_merchant, make_user, new_client
from .pay import advance, attempt_rows, mock_id, pay, pos_request, setup_shop, token_of, view


async def _approve(customer, token):
    r = await pay(customer, token)
    assert r.status_code == 200, r.text
    url = r.json()["authorisation_url"]
    assert (await customer.post(f"/v1/mock-bank/{mock_id(url)}/approve")).status_code == 200
    return url


# ---------------------------------------------------------------- outcomes


async def test_customer_cancels_at_bank_then_pays(app, shop, owner_engine):
    customer = new_client(app)
    req = await pos_request(shop, shop.network, 1200)
    token = token_of(req)
    url = (await pay(customer, token)).json()["authorisation_url"]
    await customer.post(f"/v1/mock-bank/{mock_id(url)}/cancel")
    await shop.work()
    v = await view(customer, token)
    assert v["state"] == "cancelled" and v["can_pay"] is True
    await _approve(customer, token)
    await shop.work()
    assert (await view(customer, token))["state"] == "paid"
    assert [a["status"] for a in await attempt_rows(owner_engine, token)] == ["CANCELLED", "SENT"]
    assert shop.inbox.types() == ["payment.succeeded"]


@pytest.mark.parametrize("amount,reason", [(515, "REJECTED"), (503, "BANK_ERROR"),
                                           (502, "INTERNAL_ERROR"), (10515, "REJECTED")])
async def test_payment_failure_allows_retry(app, shop, owner_engine, amount, reason):
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, amount))
    await _approve(customer, token)
    await shop.work()
    v = await view(customer, token)
    assert v["state"] == "failed" and v["can_pay"] is True
    rows = await attempt_rows(owner_engine, token)
    assert rows[-1]["status"] == "FAILED" and rows[-1]["status_reason"] == reason
    assert shop.inbox.types() == []  # nothing final for the POS yet


async def test_delayed_success(app, shop):
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 523))
    await _approve(customer, token)
    await shop.work()
    assert (await view(customer, token))["state"] == "processing"
    advance(181)
    await shop.work()
    assert (await view(customer, token))["state"] == "paid"


async def test_delayed_failure(app, shop):
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 522))
    await _approve(customer, token)
    await shop.work()
    assert (await view(customer, token))["state"] == "processing"
    advance(31)
    await shop.work()
    assert (await view(customer, token))["state"] == "failed"


async def test_indeterminate_bank_error_goes_to_review_and_is_never_retried(app, shop,
                                                                             owner_engine):
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 504))
    await _approve(customer, token)
    await shop.work()
    v = await view(customer, token)
    assert v["state"] == "unconfirmed" and v["can_pay"] is False
    r = await pay(customer, token)
    assert r.status_code == 409  # never a second charge attempt
    assert shop.inbox.types() == ["payment.requires_review"]
    queue = (await shop.admin.get("/v1/admin/payments/review")).json()["items"]
    item = next(i for i in queue if i["reference"] == v["reference"])
    attempt_id = item["attempts"][0]["id"]
    # Resolution needs evidence, and only works for attempts in review.
    r = await shop.admin.post(f"/v1/admin/payment-attempts/{attempt_id}/resolve",
                              json={"outcome": "sent", "evidence": "short"})
    assert r.status_code == 422
    r = await shop.admin.post(f"/v1/admin/payment-attempts/{attempt_id}/resolve",
                              json={"outcome": "sent",
                                    "evidence": "Seen on merchant statement 7 Nov, ref SP…"})
    assert r.status_code == 200, r.text
    await shop.work()
    assert (await view(customer, token))["state"] == "paid"
    assert shop.inbox.types() == ["payment.requires_review", "payment.succeeded"]


async def test_no_final_status_after_a_day_needs_review(app, shop, owner_engine):
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 520))
    await _approve(customer, token)
    await shop.work()
    # The provider never answers again (simulate by freezing the mock's delayed outcome).
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE mock_provider.payments SET settle_at = NULL"))
    advance(25 * 3600)
    await shop.work()
    assert (await view(customer, token))["state"] == "unconfirmed"


# ---------------------------------------------------------------- provider and network trouble


async def test_provider_create_error_is_clean_failure(app, shop, owner_engine):
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 501))
    r = await pay(customer, token)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "provider_unavailable"
    rows = await attempt_rows(owner_engine, token)
    assert rows[-1]["status"] == "FAILED" and rows[-1]["provider_payment_id"] is None
    assert (await view(customer, token))["can_pay"] is True


async def test_bank_timeout_on_create(app, shop, owner_engine):
    """The provider created a payment but we never got the link: no one can approve it."""
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 505))
    r = await pay(customer, token)
    assert r.status_code == 503
    rows = await attempt_rows(owner_engine, token)
    assert rows[-1]["status"] == "FAILED" and rows[-1]["status_reason"] == "create_provider_timeout"


async def test_provider_downtime_during_polling_changes_nothing(app, shop, owner_engine,
                                                              monkeypatch):
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 520))
    await _approve(customer, token)
    await shop.work()

    async def down(_pid):
        raise ProviderError(ErrorKind.TEMPORARY, "unavailable", "provider down")

    provider = get_provider()
    monkeypatch.setattr(provider, "get_payment", down)
    advance(31)
    await shop.work()
    before = (await attempt_rows(owner_engine, token))[-1]
    assert before["status"] == "PROCESSING" and before["next_poll_at"] is not None
    monkeypatch.undo()
    advance(400)
    await shop.work()
    assert (await view(customer, token))["state"] == "paid"


async def test_missing_webhooks_recovered_by_polling(app, shop):
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 506))  # mock sends no webhooks
    await _approve(customer, token)
    await shop.work(rounds=1)
    advance(10)
    await shop.work()
    assert (await view(customer, token))["state"] == "paid"
    assert shop.inbox.types() == ["payment.succeeded"]


async def test_replayed_webhooks_are_ignored(app, shop, owner_engine):
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 507))  # every webhook sent twice
    await _approve(customer, token)
    await shop.work()
    with owner_engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.is_platform_admin', 'true', false)"))
        outcomes = conn.execute(text("SELECT count(*) FROM payment_provider_events")).scalar()
        hook = conn.execute(text("SELECT target, body, signature, key_id FROM"
                                 " mock_provider.webhooks ORDER BY id DESC LIMIT 1")).one()
    assert outcomes >= 1
    # An attacker (or the provider) replays a captured, validly signed webhook.
    r = await shop.network.post(hook.target, content=hook.body, headers={
        "x-akahu-payments-signature": hook.signature, "x-akahu-payments-signing-key": hook.key_id})
    assert r.status_code == 200 and r.json()["duplicate"] is True
    assert shop.inbox.types() == ["payment.succeeded"]


async def test_out_of_order_webhooks_never_go_backwards(app, shop, owner_engine):
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 508))  # final webhook arrives first
    await _approve(customer, token)
    for _ in range(4):
        await shop.work()
        advance(2)
    rows = await attempt_rows(owner_engine, token)
    assert rows[-1]["status"] == "SENT"
    assert (await view(customer, token))["state"] == "paid"


async def test_forged_webhook_rejected(app, shop, owner_engine):
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 1000))
    url = (await pay(customer, token)).json()["authorisation_url"]
    body = json.dumps({"type": "ONE_OFF_PAYMENT_STATUS_UPDATE", "_payment": mock_id(url),
                       "status": "SENT", "emitted_at": "2026-11-07T00:00:00Z"})
    r = await shop.network.post("http://localhost:8000/v1/webhooks/mock", content=body, headers={
        "x-akahu-payments-signature": "Zm9yZ2Vk", "x-akahu-payments-signing-key": "mock_key_x"})
    assert r.status_code == 401
    r = await shop.network.post("http://localhost:8000/v1/webhooks/mock", content=body)
    assert r.status_code == 401
    assert (await view(customer, token))["state"] == "waiting"


async def test_server_restart_during_processing(app, shop, owner_engine):
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 520))
    await _approve(customer, token)
    # The API and worker restart: new app instance, nothing in memory carries over.
    import httpx

    from app.main import create_app
    from tests.pay import Network
    fresh = create_app()
    network = httpx.AsyncClient(transport=Network(fresh, shop.inbox), base_url="http://testserver")
    from app.worker import run_once
    advance(31)
    for _ in range(3):
        await run_once(network)
    assert (await view(new_client(fresh), token))["state"] == "paid"


async def test_crash_before_provider_answers_is_recovered(app, shop, owner_engine, monkeypatch):
    from app.payments import service

    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 900))
    provider = get_provider()
    real = provider.create_payment

    async def crash(_request):
        raise asyncio.CancelledError  # the process dies mid-call

    monkeypatch.setattr(provider, "create_payment", crash)
    with pytest.raises(asyncio.CancelledError):
        await service.start_attempt(shop.merchant["id"], token, provider)
    monkeypatch.setattr(provider, "create_payment", real)
    assert (await attempt_rows(owner_engine, token))[-1]["status"] == "CREATED"
    r = await pay(customer, token)
    assert r.status_code == 409 and r.json()["error"]["code"] == "payment_starting"
    advance(6 * 60)
    await shop.work()
    assert (await attempt_rows(owner_engine, token))[-1]["status"] == "FAILED"
    await _approve(customer, token)
    await shop.work()
    assert (await view(customer, token))["state"] == "paid"


# ---------------------------------------------------------------- expiry


async def test_expired_pos_qr(app, shop):
    customer = new_client(app)
    req = await pos_request(shop, shop.network, 1000, expires=60)
    token = token_of(req)
    await pay(customer, token)  # opened the bank page, then walked away
    advance(61)
    await shop.work()
    v = await view(customer, token)
    assert v["state"] == "expired" and v["can_pay"] is False
    assert (await pay(customer, token)).status_code == 409
    assert shop.inbox.types() == ["payment.expired"]


async def test_expiry_never_cancels_an_approved_payment(app, shop):
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 523, expires=60))
    await _approve(customer, token)
    advance(61)
    await shop.work()
    assert (await view(customer, token))["state"] == "processing"
    advance(181)
    await shop.work()
    assert (await view(customer, token))["state"] == "paid"
    assert shop.inbox.types() == ["payment.succeeded"]


async def test_expired_payment_link(app, shop):
    from datetime import timedelta

    from app.core import clock
    expires = (clock.now() + timedelta(hours=1)).isoformat()
    code = (await shop.owner.post(f"/v1/merchants/{shop.mid}/payment-links", json={
        "amount_cents": 500, "description": "Invoice 12", "expires_at": expires})).json()["code"]
    customer = new_client(app)
    advance(3601)
    assert (await customer.get(f"/v1/checkout/links/{code}")).json()["state"] == "expired"
    r = await customer.post(f"/v1/checkout/links/{code}/requests", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "link_expired"


# ---------------------------------------------------------------- tampering and duplicates


@pytest.mark.parametrize("amount", [0, -100, 12.5, "1250", True, 10**12])
async def test_manipulated_amounts_rejected(app, shop, amount):
    customer = new_client(app)
    r = await customer.post(f"/v1/checkout/merchants/{shop.merchant['slug']}/requests",
                            json={"amount_cents": amount})
    assert r.status_code == 422
    r = await shop.network.post("/v1/pos/payment-requests", headers=shop.pos_headers("k-amt"),
                                json={"external_order_id": "amt", "amount_cents": amount})
    assert r.status_code == 422


async def test_client_cannot_supply_destination_or_amount_at_pay(app, shop, mock):
    customer = new_client(app)
    r = await customer.post(f"/v1/checkout/merchants/{shop.merchant['slug']}/requests",
                            json={"amount_cents": 100, "payee_account": "99-9999-9999999-99"})
    assert r.status_code == 422
    token = token_of(await pos_request(shop, shop.network, 1000))
    r = await customer.post(f"/v1/checkout/requests/{token}/pay",
                            json={"amount_cents": 1, "account_number": "99-9999-9999999-99"})
    url = r.json()["authorisation_url"]
    bank = await mock.bank_view(mock_id(url))
    assert bank["amount_cents"] == 1000 and bank["payee_account"] == "12-3456-7890123-00"


async def test_bank_account_change_never_affects_existing_payment(app, shop, mock, owner_engine):
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 1000))
    url = (await pay(customer, token)).json()["authorisation_url"]
    # The merchant switches to a new verified account while the customer is at the bank.
    r = await shop.owner.post(f"/v1/merchants/{shop.mid}/bank-accounts", json={
        "account_number": "02-0000-1111111-000", "holder_name": "New Account"})
    assert r.status_code == 201
    await shop.admin.post(f"/v1/admin/bank-accounts/{r.json()['id']}/verify",
                          json={"reason": "new statement"})
    await customer.post(f"/v1/mock-bank/{mock_id(url)}/approve")
    await shop.work()
    row = (await attempt_rows(owner_engine, token))[-1]
    assert row["status"] == "SENT" and row["destination_name"] == "Test Shop"
    assert (await mock.bank_view(mock_id(url)))["payee_account"] == "12-3456-7890123-00"
    # And the snapshot can't be edited, even directly in the database by the app role.
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings
    engine = create_async_engine(get_settings().database_url)
    try:
        with pytest.raises(Exception, match="immutable"):
            async with engine.begin() as conn:
                await conn.execute(text("SELECT set_config('app.is_platform_admin','true',true)"))
                await conn.execute(text("UPDATE payment_attempts SET destination_name = 'x'"
                                        " WHERE id = :id"), {"id": row["id"]})
    finally:
        await engine.dispose()


async def test_merchant_without_verified_account_cannot_take_payments(app, clock, owner_engine):
    shop = await setup_shop(app, clock, owner_engine, verified=False)
    customer = new_client(app)
    r = await customer.post(f"/v1/checkout/merchants/{shop.merchant['slug']}/requests",
                            json={"amount_cents": 500})
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_verified_account"


async def test_unapproved_merchant_is_not_reachable(app, clock):
    owner = await make_user(app, clock, "draft")
    m = await make_merchant(owner)
    r = await new_client(app).get(f"/v1/checkout/merchants/{m['slug']}")
    assert r.status_code == 404


async def test_pos_idempotency_and_duplicate_orders(app, shop):
    body = {"external_order_id": "B-7", "amount_cents": 1100}
    first = await shop.network.post("/v1/pos/payment-requests",
                                    headers=shop.pos_headers("key-1"), json=body)
    again = await shop.network.post("/v1/pos/payment-requests",
                                    headers=shop.pos_headers("key-1"), json=body)
    assert first.status_code == 201 and again.status_code == 201
    assert first.json()["id"] == again.json()["id"]
    changed = await shop.network.post("/v1/pos/payment-requests", headers=shop.pos_headers("key-1"),
                                      json={**body, "amount_cents": 1}),
    assert changed[0].status_code == 409
    assert changed[0].json()["error"]["code"] == "idempotency_conflict"
    dup = await shop.network.post("/v1/pos/payment-requests", headers=shop.pos_headers("key-2"),
                                  json=body)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "order_has_payment_request"
    assert (await shop.network.post("/v1/pos/payment-requests", headers=shop.pos_headers(),
                                    json=body)).status_code == 400
    # After cancelling, the same order can get a new request.
    rid = first.json()["id"]
    r = await shop.network.post(f"/v1/pos/payment-requests/{rid}/cancel",
                                headers=shop.pos_headers())
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert (await shop.network.post("/v1/pos/payment-requests", headers=shop.pos_headers("key-3"),
                                    json=body)).status_code == 201


async def test_concurrent_pay_taps_create_one_attempt(app, shop, owner_engine):
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 1000))
    results = await asyncio.gather(*[pay(customer, token) for _ in range(5)])
    ok = [r for r in results if r.status_code == 200]
    assert ok and all(r.status_code in (200, 409) for r in results)
    assert len({r.json()["authorisation_url"] for r in ok}) == 1
    assert len(await attempt_rows(owner_engine, token)) == 1


async def test_cannot_cancel_after_customer_approved(app, shop):
    customer = new_client(app)
    req = await pos_request(shop, shop.network, 520)
    await _approve(customer, token_of(req))
    r = await shop.network.post(f"/v1/pos/payment-requests/{req['id']}/cancel",
                                headers=shop.pos_headers())
    assert r.status_code == 409 and r.json()["error"]["code"] == "payment_in_progress"


async def test_cancel_while_customer_is_at_bank(app, shop, mock):
    customer = new_client(app)
    req = await pos_request(shop, shop.network, 1000)
    url = (await pay(customer, token_of(req))).json()["authorisation_url"]
    r = await shop.network.post(f"/v1/pos/payment-requests/{req['id']}/cancel",
                                headers=shop.pos_headers())
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert (await mock.bank_view(mock_id(url)))["status"] == "CANCELLED"
    # The customer can no longer approve it.
    assert (await customer.post(f"/v1/mock-bank/{mock_id(url)}/approve")).status_code == 409
    assert shop.inbox.types() == [] or shop.inbox.types() == ["payment.cancelled"]
    await shop.work()
    assert shop.inbox.types() == ["payment.cancelled"]


async def test_pos_webhook_retries_until_delivered(app, shop):
    customer = new_client(app)
    shop.inbox.fail_next = 2
    await _approve(customer, token_of(await pos_request(shop, shop.network, 1000)))
    await shop.work()
    assert shop.inbox.types() == []
    advance(15)
    await shop.work()
    advance(35)
    await shop.work()
    assert shop.inbox.types() == ["payment.succeeded"]
    deliveries = (await shop.owner.get(f"/v1/merchants/{shop.mid}/webhook-deliveries")
                  ).json()["items"]
    assert deliveries[0]["status"] == "delivered" and deliveries[0]["attempts"] == 3


# ---------------------------------------------------------------- isolation


async def test_cross_merchant_access(app, clock, owner_engine, shop):
    other = await setup_shop(app, clock, owner_engine)
    req = await pos_request(shop, shop.network, 1000)
    # Another merchant's API key and dashboard can't see it.
    r = await shop.network.get(f"/v1/pos/payment-requests/{req['id']}",
                               headers=other.pos_headers())
    assert r.status_code == 404
    r = await other.owner.get(f"/v1/merchants/{other.mid}/payment-requests/{req['id']}")
    assert r.status_code == 404
    r = await other.owner.get(f"/v1/merchants/{shop.mid}/payment-requests")
    assert r.status_code == 404
    assert req["id"] not in str((await other.owner.get(
        f"/v1/merchants/{other.mid}/payment-requests")).json())


async def test_api_key_rules(app, shop):
    bad = {"authorization": "Bearer sp_test_nope_nope"}
    assert (await shop.network.get("/v1/pos/payment-requests?external_order_id=x",
                                   headers=bad)).status_code == 401
    wrong_secret = shop.api_key[:-4] + "AAAA"
    assert (await shop.network.get("/v1/pos/payment-requests?external_order_id=x", headers={
        "authorization": f"Bearer {wrong_secret}"})).status_code == 401
    # Switching the integration off is the feature flag: the POS is refused at once.
    await shop.owner.patch(f"/v1/merchants/{shop.mid}/integrations/{shop.integration_id}",
                           json={"enabled": False})
    r = await shop.network.get("/v1/pos/payment-requests?external_order_id=x",
                               headers=shop.pos_headers())
    assert r.status_code == 403
    await shop.owner.patch(f"/v1/merchants/{shop.mid}/integrations/{shop.integration_id}",
                           json={"enabled": True})
    keys = (await shop.owner.get(f"/v1/merchants/{shop.mid}/integrations")).json()["items"][0]
    key_id = keys["api_keys"][0]["id"]
    await shop.owner.post(
        f"/v1/merchants/{shop.mid}/integrations/{shop.integration_id}/api-keys/{key_id}/revoke")
    r = await shop.network.get("/v1/pos/payment-requests?external_order_id=x",
                               headers=shop.pos_headers())
    assert r.status_code == 401


async def test_api_keys_are_stored_hashed(shop, owner_engine):
    with owner_engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.is_platform_admin', 'true', false)"))
        stored = conn.execute(text("SELECT key_hash, prefix FROM api_keys")).all()
    assert all(shop.api_key.encode() not in bytes(k) for k, _ in stored)


async def test_sensitive_changes_need_step_up(app, clock, shop, owner_engine):
    from datetime import UTC, datetime, timedelta
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE user_sessions SET mfa_verified_at = :t WHERE user_id = :id"),
                     {"t": datetime.now(UTC) - timedelta(minutes=30), "id": shop.owner.id})
    for path, body in ((f"/v1/merchants/{shop.mid}/bank-accounts",
                        {"account_number": "12-3456-7890123-00", "holder_name": "X"}),
                       (f"/v1/merchants/{shop.mid}/integrations", {"name": "x"})):
        r = await shop.owner.post(path, json=body)
        assert r.status_code == 401 and r.json()["error"]["code"] == "step_up_required"
    await shop.owner.post("/v1/auth/step-up", json={"code": code_for(shop.owner, clock)})
    assert (await shop.owner.post(f"/v1/merchants/{shop.mid}/integrations", json={"name": "x"})
            ).status_code == 201


async def test_cashier_cannot_manage_bank_or_integrations(app, clock, shop):
    cashier = await make_user(app, clock, "cashier")
    await shop.owner.post(f"/v1/merchants/{shop.mid}/members",
                          json={"email": cashier.email, "role": "cashier"})
    r = await cashier.post(f"/v1/merchants/{shop.mid}/payment-requests", json={"amount_cents": 500})
    assert r.status_code == 201  # cashiers can take payments
    for path, body in ((f"/v1/merchants/{shop.mid}/bank-accounts",
                        {"account_number": "12-3456-7890123-00", "holder_name": "X"}),
                       (f"/v1/merchants/{shop.mid}/integrations", {"name": "x"})):
        assert (await cashier.post(path, json=body)).status_code in (401, 403)


async def test_only_admin_can_verify_bank_accounts(app, shop, owner_engine):
    r = await shop.owner.post(f"/v1/merchants/{shop.mid}/bank-accounts", json={
        "account_number": "01-0001-0000001-00", "holder_name": "Sneaky"})
    acct = r.json()["id"]
    assert (await shop.owner.post(f"/v1/admin/bank-accounts/{acct}/verify",
                                  json={"reason": "self"})).status_code == 404
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings
    engine = create_async_engine(get_settings().database_url)
    try:
        with pytest.raises(Exception, match="platform admin"):
            async with engine.begin() as conn:
                await conn.execute(text("SELECT set_config('app.merchant_id', :m, true)"),
                                   {"m": shop.mid})
                await conn.execute(text("UPDATE merchant_bank_accounts SET status = 'verified'"
                                        " WHERE id = :id"), {"id": acct})
    finally:
        await engine.dispose()
