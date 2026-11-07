"""Test fixtures. Tests run against a real PostgreSQL database (no mocks for the DB),
because row-level security, triggers and constraints are part of what we test."""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path

# Configure before the app is imported (settings are cached).
_OUTBOX = tempfile.mkdtemp(prefix="stllpay-outbox-")
os.environ.update({
    "ENVIRONMENT": "test",
    "DATABASE_URL": os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://stllpay_app:devapp@localhost:5432/stllpay_test"),
    "MIGRATION_DATABASE_URL": os.environ.get(
        "TEST_MIGRATION_DATABASE_URL",
        "postgresql+psycopg://stllpay_owner:devowner@localhost:5432/stllpay_test"),
    "EMAIL_BACKEND": "file",
    "EMAIL_OUTBOX_DIR": _OUTBOX,
    "WEB_ORIGIN": "http://localhost:3000",
})

import pyotp  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402

from app.core import security  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.ratelimit import limiter  # noqa: E402
from app.main import create_app  # noqa: E402

API_DIR = Path(__file__).resolve().parents[1]
PASSWORD = "Correct-Horse-42-battery"
ORIGIN = "http://localhost:3000"


def _alembic_config() -> Config:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    return cfg


@pytest.fixture(scope="session")
def owner_engine() -> Iterator[Engine]:
    engine = create_engine(get_settings().migration_database_url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {get_settings().app_db_role}"))
    cfg = _alembic_config()
    # Exercise the downgrade path once, then build the schema for the run.
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield engine
    engine.dispose()


class FakeClock:
    """Controls time for TOTP so tests can produce fresh, non-replayed codes."""

    def __init__(self) -> None:
        import time as _time
        self.now = _time.time()

    def time(self) -> float:
        return self.now

    def tick(self, seconds: float = 30) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(security, "time", fake)
    return fake


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    limiter.reset()


@pytest.fixture
async def app(owner_engine: Engine) -> AsyncIterator[object]:
    yield create_app()


def new_client(app: object) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver",  # type: ignore[arg-type]
                       headers={"origin": ORIGIN})


@pytest.fixture
async def client(app: object) -> AsyncIterator[AsyncClient]:
    async with new_client(app) as c:
        yield c


def csrf(client: AsyncClient) -> dict[str, str]:
    token = client.cookies.get(get_settings().csrf_cookie_name)
    return {"x-csrf-token": token} if token else {}


def latest_email_to(address: str) -> dict[str, str]:
    files = sorted(Path(_OUTBOX).glob("*.json"))
    for f in reversed(files):
        msg = json.loads(f.read_text())
        if msg["to"] == address:
            return msg
    raise AssertionError(f"no email to {address}")


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


@dataclass
class TestUser:
    __test__ = False
    email: str
    client: AsyncClient
    secret: str = ""
    id: str = ""
    extra: dict[str, object] = field(default_factory=dict)

    async def post(self, url: str, **kw: object):  # type: ignore[no-untyped-def]
        return await self.client.post(url, headers=csrf(self.client), **kw)  # type: ignore[arg-type]

    async def patch(self, url: str, **kw: object):  # type: ignore[no-untyped-def]
        return await self.client.patch(url, headers=csrf(self.client), **kw)  # type: ignore[arg-type]

    async def delete(self, url: str, **kw: object):  # type: ignore[no-untyped-def]
        return await self.client.delete(url, headers=csrf(self.client), **kw)  # type: ignore[arg-type]

    async def get(self, url: str, **kw: object):  # type: ignore[no-untyped-def]
        return await self.client.get(url, **kw)  # type: ignore[arg-type]


async def signup_and_verify(client: AsyncClient, email: str) -> None:
    r = await client.post("/v1/auth/signup", json={"email": email, "password": PASSWORD})
    assert r.status_code == 202, r.text
    body = latest_email_to(email)["body"]
    token = re.search(r"token=([\w-]+)", body).group(1)  # type: ignore[union-attr]
    r = await client.post("/v1/auth/verify-email", json={"token": token})
    assert r.status_code == 200, r.text


async def make_user(app: object, clock: FakeClock, prefix: str = "user") -> TestUser:
    """A verified user, logged in with MFA enrolled and verified."""
    client = new_client(app)
    email = unique_email(prefix)
    await signup_and_verify(client, email)
    r = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    r = await client.post("/v1/auth/mfa/setup", headers=csrf(client))
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    clock.tick()
    r = await client.post("/v1/auth/mfa/enable", headers=csrf(client),
                          json={"code": pyotp.TOTP(secret).at(clock.now)})
    assert r.status_code == 200, r.text
    me = (await client.get("/v1/me")).json()
    return TestUser(email=email, client=client, secret=secret, id=me["id"])


def code_for(user: TestUser, clock: FakeClock) -> str:
    clock.tick()
    return pyotp.TOTP(user.secret).at(clock.now)


async def make_merchant(user: TestUser, slug: str | None = None) -> dict[str, object]:
    slug = slug or f"m-{uuid.uuid4().hex[:10]}"
    r = await user.post("/v1/merchants", json={
        "slug": slug, "legal_name": f"{slug} Limited", "trading_name": slug.upper()})
    assert r.status_code == 201, r.text
    return r.json()


def make_admin(owner_engine: Engine, user: TestUser) -> None:
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE users SET is_platform_admin = true WHERE id = :id"),
                     {"id": user.id})
