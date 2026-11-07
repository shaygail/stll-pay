"""Payment journeys end to end against the mock provider: counter QR, POS QR, links."""

from __future__ import annotations

from .conftest import new_client
from .pay import advance, attempt_rows, mock_id, pay, pos_request, token_of, view


async def test_counter_qr_success(app, shop, mock, owner_engine):
    customer = new_client(app)
    page = (await customer.get(f"/v1/checkout/merchants/{shop.merchant['slug']}")).json()
    assert page["trading_name"] == shop.merchant["trading_name"]
    assert page["accepting_payments"] is True and page["test_mode"] is True

    r = await customer.post(f"/v1/checkout/merchants/{shop.merchant['slug']}/requests",
                            json={"amount_cents": 1250, "note": "2 coffees"})
    assert r.status_code == 201, r.text
    token = r.json()["checkout_token"]
    v = await view(customer, token)
    assert v["state"] == "ready" and v["can_pay"] and v["amount_cents"] == 1250

    r = await pay(customer, token)
    assert r.status_code == 200, r.text
    url = r.json()["authorisation_url"]
    assert "/mock-bank/" in url
    assert (await view(customer, token))["state"] == "waiting"

    # The mock bank receives the immutable snapshot, not anything the browser sent.
    bank = (await customer.get(f"/v1/mock-bank/{mock_id(url)}")).json()
    assert bank["amount_cents"] == 1250 and bank["payee_name"] == "Test Shop"
    assert bank["payee_reference"] == v["reference"]

    assert (await customer.post(f"/v1/mock-bank/{mock_id(url)}/approve")).status_code == 200
    await shop.work()
    v = await view(customer, token)
    assert v["state"] == "paid" and v["paid_at"]
    rows = await attempt_rows(owner_engine, token)
    assert [r["status"] for r in rows] == ["SENT"]


async def test_return_from_bank_alone_never_marks_paid(app, shop):
    """Approved at the bank, but the provider hasn't confirmed yet (5.20 = 30 s delay)."""
    customer = new_client(app)
    req = await pos_request(shop, shop.network, 520)
    token = token_of(req)
    url = (await pay(customer, token)).json()["authorisation_url"]
    await customer.post(f"/v1/mock-bank/{mock_id(url)}/approve")
    await shop.work()
    # Customer is back on the return page, refreshing: still processing, not paid.
    v = await view(customer, token, refresh=True)
    assert v["state"] == "processing"
    assert "payment.succeeded" not in shop.inbox.types()
    advance(31)
    await shop.work()
    assert (await view(customer, token, refresh=True))["state"] == "paid"


async def test_pos_dynamic_qr_success_notifies_pos_once(app, shop):
    customer = new_client(app)
    req = await pos_request(shop, shop.network, 899, order="A-42")
    assert req["status"] == "open" and req["external_order_id"] == "A-42"
    url = (await pay(customer, token_of(req))).json()["authorisation_url"]
    await customer.post(f"/v1/mock-bank/{mock_id(url)}/approve")
    for _ in range(4):  # webhooks, polls, more polls: still only one "paid" to the POS
        await shop.work()
        advance(400)
    assert shop.inbox.types() == ["payment.succeeded"]
    event = shop.inbox.events[0]
    assert event["data"]["payment_request"]["external_order_id"] == "A-42"
    assert event["data"]["payment_request"]["status"] == "paid"
    r = await shop.network.get(f"http://testserver/v1/pos/payment-requests/{req['id']}",
                               headers=shop.pos_headers())
    assert r.json()["status"] == "paid"


async def test_customer_closes_browser_after_approving(app, shop):
    """No return visit at all: the webhook and the worker still complete the payment."""
    customer = new_client(app)
    req = await pos_request(shop, shop.network, 1500)
    url = (await pay(customer, token_of(req))).json()["authorisation_url"]
    await customer.post(f"/v1/mock-bank/{mock_id(url)}/approve")
    del customer
    await shop.work()
    assert shop.inbox.types() == ["payment.succeeded"]


async def test_reopening_checkout_resumes_same_bank_step(app, shop, owner_engine):
    customer = new_client(app)
    req = await pos_request(shop, shop.network, 700)
    first = (await pay(customer, token_of(req))).json()["authorisation_url"]
    second = (await pay(customer, token_of(req))).json()["authorisation_url"]
    assert first == second
    assert len(await attempt_rows(owner_engine, token_of(req))) == 1
    assert (await view(customer, token_of(req)))["resume_url"] == first


async def test_fixed_amount_payment_link(app, shop):
    r = await shop.owner.post(f"/v1/merchants/{shop.mid}/payment-links",
                              json={"amount_cents": 4500, "description": "Catering deposit",
                                    "max_uses": 1})
    assert r.status_code == 201, r.text
    code = r.json()["code"]
    customer = new_client(app)
    page = (await customer.get(f"/v1/checkout/links/{code}")).json()
    assert page["state"] == "active" and page["amount_cents"] == 4500
    # The customer can't change a fixed amount.
    r = await customer.post(f"/v1/checkout/links/{code}/requests", json={"amount_cents": 100})
    assert r.status_code == 422
    token = (await customer.post(f"/v1/checkout/links/{code}/requests", json={})
             ).json()["checkout_token"]
    url = (await pay(customer, token)).json()["authorisation_url"]
    await customer.post(f"/v1/mock-bank/{mock_id(url)}/approve")
    await shop.work()
    assert (await view(customer, token))["state"] == "paid"
    # Single-use link is now used up.
    assert (await customer.get(f"/v1/checkout/links/{code}")).json()["state"] == "used"
    r = await customer.post(f"/v1/checkout/links/{code}/requests", json={})
    assert r.status_code == 409


async def test_open_amount_link(app, shop):
    code = (await shop.owner.post(f"/v1/merchants/{shop.mid}/payment-links",
                                  json={"description": "Pay what you like"})).json()["code"]
    customer = new_client(app)
    assert (await customer.post(f"/v1/checkout/links/{code}/requests", json={})
            ).status_code == 422
    r = await customer.post(f"/v1/checkout/links/{code}/requests", json={"amount_cents": 2000})
    assert r.status_code == 201


async def test_static_qr_codes(app, shop):
    r = await shop.owner.post(f"/v1/merchants/{shop.mid}/qr-codes", json={"label": "Counter"})
    assert r.status_code == 201
    qr = r.json()
    assert qr["url"].endswith(f"/pay/{shop.merchant['slug']}?qr={qr['code']}")
    customer = new_client(app)
    slug = shop.merchant["slug"]
    r = await customer.post(f"/v1/checkout/merchants/{slug}/requests",
                            json={"amount_cents": 300, "qr": qr["code"]})
    assert r.status_code == 201
    await shop.owner.patch(f"/v1/merchants/{shop.mid}/qr-codes/{qr['id']}", json={"active": False})
    r = await customer.post(f"/v1/checkout/merchants/{slug}/requests",
                            json={"amount_cents": 300, "qr": qr["code"]})
    assert r.status_code == 409


async def test_dashboard_summary_separates_received_from_processing(app, shop):
    customer = new_client(app)
    paid = await pos_request(shop, shop.network, 1000)
    url = (await pay(customer, token_of(paid))).json()["authorisation_url"]
    await customer.post(f"/v1/mock-bank/{mock_id(url)}/approve")
    slow = await pos_request(shop, shop.network, 520)
    url = (await pay(customer, token_of(slow))).json()["authorisation_url"]
    await customer.post(f"/v1/mock-bank/{mock_id(url)}/approve")
    await shop.work()
    s = (await shop.owner.get(f"/v1/merchants/{shop.mid}/summary")).json()
    assert s["received"] == {"count": 1, "amount_cents": 1000}
    assert s["processing"] == {"count": 1, "amount_cents": 520}
    assert s["test_mode"] is True
    detail = (await shop.owner.get(f"/v1/merchants/{shop.mid}/payment-requests/{paid['id']}")
              ).json()
    assert [h["to"] for h in detail["history"]][-1] == "SENT"
    assert detail["attempts"][0]["destination"] == "Test Shop ··2300"
