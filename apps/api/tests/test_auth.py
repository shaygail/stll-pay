"""Sign-up, verification, login, MFA, sessions, CSRF and brute-force protection."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.core.config import get_settings

from .conftest import (
    PASSWORD,
    code_for,
    csrf,
    latest_email_to,
    make_user,
    new_client,
    signup_and_verify,
    unique_email,
)


async def test_full_signup_login_mfa_flow(app, clock):
    user = await make_user(app, clock)
    r = await user.get("/v1/me")
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == user.email
    assert body["is_platform_admin"] is False
    assert body["memberships"] == []


async def test_session_cookie_flags(client):
    email = unique_email()
    await signup_and_verify(client, email)
    r = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    cookies = r.headers.get_list("set-cookie")
    session = next(c for c in cookies if c.startswith(get_settings().session_cookie_name))
    assert "HttpOnly" in session
    assert "samesite=lax" in session.lower()
    csrf_cookie = next(c for c in cookies if c.startswith(get_settings().csrf_cookie_name))
    assert "HttpOnly" not in csrf_cookie


async def test_weak_password_rejected(client):
    r = await client.post("/v1/auth/signup", json={"email": unique_email(), "password": "short"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "weak_password"


async def test_signup_does_not_reveal_existing_accounts(client):
    email = unique_email()
    first = await client.post("/v1/auth/signup", json={"email": email, "password": PASSWORD})
    second = await client.post("/v1/auth/signup", json={"email": email, "password": PASSWORD})
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert "Someone tried" in latest_email_to(email)["subject"]


async def test_verification_link_is_single_use(client):
    email = unique_email()
    await client.post("/v1/auth/signup", json={"email": email, "password": PASSWORD})
    token = re.search(r"token=([\w-]+)", latest_email_to(email)["body"]).group(1)
    assert (await client.post("/v1/auth/verify-email", json={"token": token})).status_code == 200
    r = await client.post("/v1/auth/verify-email", json={"token": token})
    assert r.status_code == 400


async def test_unverified_email_cannot_log_in(client):
    email = unique_email()
    await client.post("/v1/auth/signup", json={"email": email, "password": PASSWORD})
    r = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "email_unverified"


async def test_wrong_password_and_unknown_email_look_the_same(client):
    email = unique_email()
    await signup_and_verify(client, email)
    wrong = await client.post("/v1/auth/login", json={"email": email, "password": "Nope-nope-123"})
    unknown = await client.post("/v1/auth/login",
                                json={"email": unique_email(), "password": "Nope-nope-123"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_account_locks_after_repeated_failures(client):
    email = unique_email()
    await signup_and_verify(client, email)
    for _ in range(get_settings().login_max_failures):
        r = await client.post("/v1/auth/login", json={"email": email, "password": "Wrong-pass-123"})
        assert r.status_code == 401
    # Even the right password is refused while locked.
    r = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 423


async def test_login_rate_limited_per_email(client):
    email = unique_email()
    statuses = [
        (await client.post("/v1/auth/login",
                           json={"email": email, "password": "Wrong-pass-123"})).status_code
        for _ in range(11)
    ]
    assert statuses[-1] == 429


async def test_password_alone_is_not_enough(client):
    email = unique_email()
    await signup_and_verify(client, email)
    r = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.json() == {"mfa_required": True, "mfa_enrolled": False}
    r = await client.get("/v1/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "mfa_required"
    r = await client.get("/v1/auth/session")
    assert r.json()["mfa_verified"] is False


async def test_mfa_login_and_session_rotation(app, clock):
    user = await make_user(app, clock)
    await user.post("/v1/auth/logout")
    client = new_client(app)
    await client.post("/v1/auth/login", json={"email": user.email, "password": PASSWORD})
    pre_mfa_token = client.cookies.get(get_settings().session_cookie_name)
    r = await client.post("/v1/auth/mfa/verify", headers=csrf(client),
                          json={"code": code_for(user, clock)})
    assert r.status_code == 200, r.text
    assert (await client.get("/v1/me")).status_code == 200
    # The pre-MFA token no longer works.
    stale = new_client(app)
    stale.cookies.set(get_settings().session_cookie_name, pre_mfa_token)
    assert (await stale.get("/v1/auth/session")).status_code == 401


async def test_totp_code_cannot_be_replayed(app, clock):
    user = await make_user(app, clock)
    code = code_for(user, clock)
    assert (await user.post("/v1/auth/step-up", json={"code": code})).status_code == 200
    r = await user.post("/v1/auth/step-up", json={"code": code})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_code"


async def test_wrong_totp_code_rejected(app, clock):
    user = await make_user(app, clock)
    r = await user.post("/v1/auth/step-up", json={"code": "000000"})
    assert r.status_code == 401


async def test_mfa_secret_is_encrypted_at_rest(app, clock, owner_engine):
    user = await make_user(app, clock)
    with owner_engine.connect() as conn:
        stored = conn.execute(text("SELECT mfa_secret_enc FROM users WHERE id = :id"),
                              {"id": user.id}).scalar()
    assert stored and user.secret not in stored


async def test_unsafe_requests_need_csrf_token(app, clock):
    user = await make_user(app, clock)
    r = await user.client.post("/v1/merchants", json={
        "slug": "csrf-test", "legal_name": "X", "trading_name": "X"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_failed"


async def test_cross_origin_requests_rejected(app, clock):
    user = await make_user(app, clock)
    r = await user.client.post("/v1/merchants", headers={**csrf(user.client),
                                                          "origin": "https://evil.example"},
                               json={"slug": "evil-test", "legal_name": "X", "trading_name": "X"})
    assert r.status_code == 403
    r = await user.client.post("/v1/auth/login", headers={"origin": "https://evil.example"},
                               json={"email": user.email, "password": PASSWORD})
    assert r.status_code == 403


async def test_logout_revokes_session(app, clock):
    user = await make_user(app, clock)
    token = user.client.cookies.get(get_settings().session_cookie_name)
    assert (await user.post("/v1/auth/logout")).status_code == 200
    replay = new_client(app)
    replay.cookies.set(get_settings().session_cookie_name, token)
    assert (await replay.get("/v1/me")).status_code == 401


async def test_idle_session_expires(app, clock, owner_engine):
    user = await make_user(app, clock)
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE user_sessions SET last_seen_at = :t WHERE user_id = :id"),
                     {"t": datetime.now(UTC) - timedelta(hours=1), "id": user.id})
    r = await user.get("/v1/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "session_expired"


async def test_mfa_enrolment_cannot_be_repeated(app, clock):
    user = await make_user(app, clock)
    r = await user.post("/v1/auth/mfa/setup")
    assert r.status_code == 409


async def test_session_tokens_stored_hashed(app, clock, owner_engine):
    user = await make_user(app, clock)
    token = user.client.cookies.get(get_settings().session_cookie_name)
    with owner_engine.connect() as conn:
        hashes = conn.execute(text("SELECT token_hash FROM user_sessions WHERE user_id = :id"),
                              {"id": user.id}).scalars().all()
    assert all(token.encode() not in bytes(h) for h in hashes)

