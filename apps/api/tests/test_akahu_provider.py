"""AkahuPaymentProvider against a fake Akahu built from the documentation."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import get_settings
from app.payments.providers.akahu import AkahuPaymentProvider
from app.payments.providers.akahu_wire import cents_to_dollars, dollars_to_cents
from app.payments.providers.base import CreatePayment, ErrorKind, ProviderError, WebhookRejected
from app.payments.providers.registry import set_provider_override
from app.payments.states import Status

from .conftest import new_client
from .fake_akahu import SECRET, TOKEN, FakeAkahu
from .pay import attempt_rows, pay, pos_request, token_of, view


@pytest.fixture
def fake(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "akahu_app_token", SecretStr(TOKEN))
    monkeypatch.setattr(settings, "akahu_app_secret", SecretStr(SECRET))
    return FakeAkahu()


@pytest.fixture
def akahu(fake):
    return AkahuPaymentProvider(live=False, transport=httpx.MockTransport(fake.handler))


def _create(amount=1250, reference="SPABCDEFGH"):
    return CreatePayment(
        amount_cents=amount, payee_name="STLL HAUS", payee_account="01-0123-0123456-00",
        reference=reference, payer_reference=reference,
        redirect_uri="https://pay.stllhaus.co/c/tok/return",
        webhook_uri="https://pay.stllhaus.co/api/v1/webhooks/akahu?attempt=x",
        expires_at=datetime.now(UTC) + timedelta(minutes=20))


# ---------------------------------------------------------------- money conversion


@pytest.mark.parametrize("cents", [1, 29, 1250, 123450, 100_000_000, 999_999_999])
def test_amount_round_trip_is_exact(cents):
    assert dollars_to_cents(cents_to_dollars(cents)) == cents


def test_dollars_parse_without_float_error():
    assert dollars_to_cents(0.29) == 29
    assert dollars_to_cents("12.5") == 1250
    assert dollars_to_cents(1234.56) == 123456


# ---------------------------------------------------------------- API calls


async def test_create_sends_documented_body_with_basic_auth(akahu, fake):
    created = await akahu.create_payment(_create())
    assert created.status == "READY"
    assert created.authorisation_url.startswith("https://payments.akahu.nz/")
    method, path, body = fake.calls[-1]
    assert (method, path) == ("POST", "/v1/one-off-payments")
    assert body["amount"] == 12.5
    assert body["payee"] == {"name": "STLL HAUS", "account_number": "01-0123-0123456-00",
                             "particulars": "STLL PAY", "reference": "SPABCDEFGH"}
    assert body["payer"] == {"reference": "SPABCDEFGH"}
    assert body["webhook_uri"].endswith("/v1/webhooks/akahu?attempt=x")


async def test_get_maps_status_amount_and_payee(akahu, fake):
    created = await akahu.create_payment(_create(amount=29))
    fake.set_status(created.provider_payment_id, "FAILED", "BANK_ERROR_INDETERMINATE")
    got = await akahu.get_payment(created.provider_payment_id)
    assert (got.status, got.status_code) == ("FAILED", "BANK_ERROR_INDETERMINATE")
    assert got.amount_cents == 29 and got.payee_account == "01-0123-0123456-00"
    assert akahu.normalise(got.status, got.status_code) == Status.REQUIRES_REVIEW


@pytest.mark.parametrize("status,kind,code", [
    (500, ErrorKind.TEMPORARY, "provider_error"),
    (503, ErrorKind.TEMPORARY, "provider_error"),
    (429, ErrorKind.TEMPORARY, "provider_error"),
    (401, ErrorKind.PERMANENT, "provider_auth"),
    (400, ErrorKind.PERMANENT, "forced"),
])
async def test_create_error_mapping(akahu, fake, status, kind, code):
    fake.fail_with = status
    with pytest.raises(ProviderError) as err:
        await akahu.create_payment(_create())
    assert err.value.kind == kind and err.value.code == code


async def test_timeout_is_temporary(fake):
    def slow(_request):
        raise httpx.ReadTimeout("slow")

    provider = AkahuPaymentProvider(live=False, transport=httpx.MockTransport(slow))
    with pytest.raises(ProviderError) as err:
        await provider.create_payment(_create())
    assert err.value.kind == ErrorKind.TEMPORARY and err.value.code == "provider_timeout"


async def test_wrong_credentials(fake, monkeypatch):
    monkeypatch.setattr(get_settings(), "akahu_app_secret", SecretStr("wrong"))
    provider = AkahuPaymentProvider(live=False, transport=httpx.MockTransport(fake.handler))
    with pytest.raises(ProviderError) as err:
        await provider.create_payment(_create())
    assert err.value.code == "provider_auth"


async def test_cancel_only_while_ready(akahu, fake):
    a = await akahu.create_payment(_create())
    await akahu.cancel_payment(a.provider_payment_id)
    assert (await akahu.get_payment(a.provider_payment_id)).status_code == "APP_CANCELLED"
    b = await akahu.create_payment(_create())
    fake.set_status(b.provider_payment_id, "AUTHORISED")
    with pytest.raises(ProviderError) as err:
        await akahu.cancel_payment(b.provider_payment_id)
    assert err.value.code == "not_cancellable"


async def test_reference_over_12_characters_rejected_by_akahu(akahu):
    with pytest.raises(ProviderError) as err:
        await akahu.create_payment(_create(reference="SPTOOLONG12345"))
    assert err.value.kind == ErrorKind.PERMANENT and err.value.code == "invalid_payee"


async def test_connections(akahu):
    items = await akahu.connections()
    assert items[0]["bank"] == "demo_bank"


# ---------------------------------------------------------------- webhook signatures


async def test_webhook_signature_verified_and_key_cached(akahu, fake):
    created = await akahu.create_payment(_create())
    fake.set_status(created.provider_payment_id, "SENT")
    fake.set_status(created.provider_payment_id, "SENT")
    for _, raw, headers in fake.outbox:
        notice = await akahu.verify_webhook(raw, {k.lower(): v for k, v in headers.items()})
        assert notice.provider_payment_id == created.provider_payment_id
        assert notice.status == "SENT"
    assert fake.key_fetches == 1


async def test_tampered_or_unsigned_webhooks_rejected(akahu, fake):
    created = await akahu.create_payment(_create())
    fake.set_status(created.provider_payment_id, "CANCELLED", "USER_CANCELLED")
    _, raw, headers = fake.outbox[0]
    h = {k.lower(): v for k, v in headers.items()}
    with pytest.raises(WebhookRejected):
        await akahu.verify_webhook(raw.replace(b"CANCELLED", b"SENT     "), h)
    with pytest.raises(WebhookRejected):
        await akahu.verify_webhook(raw, {**h, "x-akahu-payments-signing-key": "key_unknown"})
    with pytest.raises(WebhookRejected):
        await akahu.verify_webhook(raw, {**h, "x-akahu-payments-signature": "not base64!"})
    with pytest.raises(WebhookRejected):
        await akahu.verify_webhook(raw, {})
    forged = base64.b64encode(b"x" * 256).decode()
    with pytest.raises(WebhookRejected):
        await akahu.verify_webhook(raw, {**h, "x-akahu-payments-signature": forged})


# ---------------------------------------------------------------- through the payment engine


@pytest.fixture
def use_akahu(akahu):
    set_provider_override(akahu)
    yield akahu
    set_provider_override(None)


async def test_engine_with_akahu_webhook_path(app, shop, fake, use_akahu, owner_engine):
    customer = new_client(app)
    req = await pos_request(shop, shop.network, 1250)
    token = token_of(req)
    r = await pay(customer, token)
    assert r.status_code == 200, r.text
    url = r.json()["authorisation_url"]
    assert url.startswith("https://payments.akahu.nz/")
    pid = url.split("payment=")[1]
    sent = fake.payments[pid]["body"]
    # Destination and amount come from the verified snapshot, never the browser.
    assert sent["amount"] == 12.5 and sent["payee"]["account_number"] == "12-3456-7890123-00"
    assert sent["redirect_uri"].endswith(f"/c/{token}/return")
    fake.approve_and_send(pid)
    codes = await fake.deliver(shop.network)
    assert all(c == 200 for c in codes)
    assert (await view(customer, token))["state"] == "paid"
    await shop.work()
    assert shop.inbox.types() == ["payment.succeeded"]
    rows = await attempt_rows(owner_engine, token)
    assert rows[-1]["provider"] == "akahu" and rows[-1]["live"] is False


async def test_engine_with_akahu_polling_path(app, shop, fake, use_akahu):
    """No webhooks reach us (for example our endpoint is down): polling still completes."""
    from .pay import advance
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 520))
    pid = (await pay(customer, token)).json()["authorisation_url"].split("payment=")[1]
    fake.approve_and_send(pid)
    fake.outbox.clear()
    advance(10)
    await shop.work()
    assert (await view(customer, token))["state"] == "paid"


async def test_engine_with_akahu_indeterminate(app, shop, fake, use_akahu):
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 504))
    pid = (await pay(customer, token)).json()["authorisation_url"].split("payment=")[1]
    fake.set_status(pid, "AUTHORISED")
    fake.set_status(pid, "FAILED", "BANK_ERROR_INDETERMINATE")
    await fake.deliver(shop.network)
    assert (await view(customer, token))["state"] == "unconfirmed"
    assert (await pay(customer, token)).status_code == 409


async def test_engine_cancel_at_akahu_when_request_expires(app, shop, fake, use_akahu):
    from .pay import advance
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 900, expires=60))
    pid = (await pay(customer, token)).json()["authorisation_url"].split("payment=")[1]
    advance(61)
    await shop.work()
    assert fake.payments[pid]["status"] == "CANCELLED"
    assert ("PUT", f"/v1/one-off-payments/{pid}/cancel", None) in fake.calls
    assert (await view(customer, token))["state"] == "expired"


async def test_engine_akahu_outage_on_create(app, shop, fake, use_akahu):
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 900))
    fake.fail_with = 503
    r = await pay(customer, token)
    assert r.status_code == 503
    assert (await view(customer, token))["can_pay"] is True
    assert (await pay(customer, token)).status_code == 200


async def test_akahu_amount_mismatch_goes_to_review(app, shop, fake, use_akahu):
    """If Akahu ever reports a different amount than we sent, a human checks it."""
    customer = new_client(app)
    token = token_of(await pos_request(shop, shop.network, 1000))
    pid = (await pay(customer, token)).json()["authorisation_url"].split("payment=")[1]
    fake.payments[pid]["body"]["amount"] = 1.0
    fake.approve_and_send(pid)
    await fake.deliver(shop.network)
    assert (await view(customer, token))["state"] == "unconfirmed"
