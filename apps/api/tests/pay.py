"""Payment test fixtures: a merchant that can take (mock) payments, a POS integration with
an API key and webhook endpoint, a fake network, and a clock that can jump ahead."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.core import clock
from app.payments.providers.registry import get_mock_provider
from app.worker import run_once

from .conftest import TestUser, code_for, make_admin, make_merchant, make_user

POS_HOST = "pos.example"


@dataclass
class PosInbox:
    """Captures webhooks the API sends to the merchant's POS, and checks their signature."""

    secret: str = ""
    host: str = field(default_factory=lambda: f"t{uuid.uuid4().hex[:10]}.{POS_HOST}")
    events: list[dict[str, Any]] = field(default_factory=list)
    fail_next: int = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.fail_next > 0:
            self.fail_next -= 1
            return httpx.Response(500)
        sig = request.headers["STLL-Signature"]
        parts = dict(p.split("=", 1) for p in sig.split(","))
        expected = hmac.new(self.secret.encode(), f"{parts['t']}.".encode() + request.content,
                            hashlib.sha256).hexdigest()
        assert hmac.compare_digest(expected, parts["v1"]), "bad STLL signature"
        self.events.append(json.loads(request.content))
        return httpx.Response(200)

    def types(self) -> list[str]:
        return [e["type"] for e in self.events]


class Network(httpx.AsyncBaseTransport):
    """Routes outbound HTTP: our API host goes to the ASGI app, the POS host to the inbox."""

    def __init__(self, app: Any, inbox: PosInbox) -> None:
        self.api = httpx.ASGITransport(app=app)
        self.inbox = inbox

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == self.inbox.host:
            return self.inbox.handle(request)
        if request.url.host.endswith(POS_HOST):
            # Another test's POS (the database is shared across tests): accept and drop.
            return httpx.Response(200)
        return await self.api.handle_async_request(request)


@dataclass
class Shop:
    owner: TestUser
    admin: TestUser
    merchant: dict[str, Any]
    account_id: str
    api_key: str
    integration_id: str
    inbox: PosInbox
    network: httpx.AsyncClient

    @property
    def mid(self) -> str:
        return str(self.merchant["id"])

    async def work(self, rounds: int = 3) -> None:
        """Run the worker a few times (mock webhooks, polling, expiry, POS deliveries)."""
        for _ in range(rounds):
            await run_once(self.network)

    def pos_headers(self, key: str | None = None) -> dict[str, str]:
        return {"authorization": f"Bearer {self.api_key}", **({"idempotency-key": key} if key
                                                               else {})}


@pytest.fixture(autouse=True)
def _reset_clock():
    clock.reset()
    yield
    clock.reset()


def advance(seconds: float) -> None:
    clock.advance(timedelta(seconds=seconds))


async def setup_shop(app: Any, clk: Any, owner_engine: Any, *, verified: bool = True) -> Shop:
    owner = await make_user(app, clk, "owner")
    admin = await make_user(app, clk, "admin")
    make_admin(owner_engine, admin)
    merchant = await make_merchant(owner)
    mid = merchant["id"]
    assert (await owner.post(f"/v1/merchants/{mid}/submit")).status_code == 200
    r = await admin.post(f"/v1/admin/merchants/{mid}/status",
                         json={"status": "restricted", "reason": "test merchant"})
    assert r.status_code == 200, r.text
    r = await owner.post(f"/v1/merchants/{mid}/bank-accounts",
                         json={"account_number": "12-3456-7890123-00", "holder_name": "Test Shop"})
    assert r.status_code == 201, r.text
    account_id = r.json()["id"]
    if verified:
        r = await admin.post(f"/v1/admin/bank-accounts/{account_id}/verify",
                             json={"reason": "statement checked"})
        assert r.status_code == 200, r.text
    r = await owner.post(f"/v1/merchants/{mid}/integrations", json={"name": "STLL POS"})
    assert r.status_code == 201, r.text
    iid = r.json()["id"]
    r = await owner.post(f"/v1/merchants/{mid}/integrations/{iid}/api-keys",
                         json={"name": "iPad till"})
    assert r.status_code == 201, r.text
    key = r.json()["key"]
    inbox = PosInbox()
    r = await owner.post(f"/v1/merchants/{mid}/integrations/{iid}/webhook-endpoints",
                         json={"url": f"http://{inbox.host}/stll-pay"})
    assert r.status_code == 201, r.text
    inbox.secret = r.json()["signing_secret"]
    network = httpx.AsyncClient(transport=Network(app, inbox), base_url="http://testserver",
                                headers={"origin": "http://localhost:3000"})
    return Shop(owner, admin, merchant, account_id, key, iid, inbox, network)


@pytest.fixture
async def shop(app, clock, owner_engine) -> Shop:
    return await setup_shop(app, clock, owner_engine)


@pytest.fixture
def mock():
    m = get_mock_provider()
    assert m is not None
    return m


async def pos_request(shop: Shop, client: httpx.AsyncClient, amount_cents: int,
                      order: str | None = None, key: str | None = None,
                      expires: int = 900) -> dict[str, Any]:
    order = order or f"order-{uuid.uuid4().hex[:12]}"
    r = await client.post("/v1/pos/payment-requests", headers=shop.pos_headers(key or order),
                          json={"external_order_id": order, "amount_cents": amount_cents,
                                "description": "Test order", "expires_in_seconds": expires})
    assert r.status_code == 201, r.text
    return r.json()


def token_of(req: dict[str, Any]) -> str:
    return req["checkout_url"].rsplit("/", 1)[-1]


async def pay(client: httpx.AsyncClient, token: str) -> httpx.Response:
    return await client.post(f"/v1/checkout/requests/{token}/pay")


def mock_id(authorisation_url: str) -> str:
    return authorisation_url.rsplit("/", 1)[-1]


async def view(client: httpx.AsyncClient, token: str, refresh: bool = False) -> dict[str, Any]:
    r = await client.get(f"/v1/checkout/requests/{token}", params={"refresh": refresh})
    assert r.status_code == 200, r.text
    return r.json()


async def attempt_rows(owner_engine: Any, token: str) -> list[dict[str, Any]]:
    with owner_engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.is_platform_admin', 'true', false)"))
        rows = conn.execute(text(
            "SELECT a.* FROM payment_attempts a JOIN payment_requests r"
            " ON r.id = a.payment_request_id WHERE r.checkout_token = :t ORDER BY a.created_at"),
            {"t": token}).mappings().all()
        return [dict(r) for r in rows]


__all__ = ["code_for"]
