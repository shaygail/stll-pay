"""Health, readiness, public config and security headers."""

from __future__ import annotations


async def test_health_and_ready(client):
    assert (await client.get("/health")).json() == {"status": "ok"}
    assert (await client.get("/ready")).json() == {"status": "ready"}


async def test_public_config_shows_safe_defaults(client):
    body = (await client.get("/v1/config")).json()
    assert body == {"environment": "test", "payments_provider": "mock",
                    "live_payments_enabled": False}


async def test_security_headers(client):
    r = await client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-request-id"]


async def test_errors_use_one_format(client):
    r = await client.get("/v1/me")
    assert r.status_code == 401
    assert set(r.json()["error"]) >= {"code", "message"}
    r = await client.post("/v1/auth/login", json={"email": "not-an-email"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"
