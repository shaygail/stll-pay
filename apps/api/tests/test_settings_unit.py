"""Settings safety checks and security primitives (no database needed)."""

from __future__ import annotations

import pyotp
import pytest
from pydantic import ValidationError

from app.core import security
from app.core.config import Settings
from app.core.ids import random_code, uuid7

PROD = {
    "environment": "production",
    "app_encryption_key": "cHJvZC1rZXktcHJvZC1rZXktcHJvZC1rZXktcHJvZDE=",
    "database_url": "postgresql+asyncpg://app:s3cret@db.example/stllpay",
    "migration_database_url": "postgresql+psycopg://owner:s3cret@db.example/stllpay",
    "web_origin": "https://pay.stllhaus.co",
}


def test_defaults_are_safe():
    s = Settings(_env_file=None)
    assert s.payments_provider == "mock"
    assert s.live_payments_enabled is False


def test_production_config_valid():
    s = Settings(_env_file=None, payments_provider="akahu", akahu_app_token="t",
                 akahu_app_secret="s", **PROD)
    assert s.secure_cookies is True
    assert s.live_payments_enabled is False


@pytest.mark.parametrize("override", [
    {"app_encryption_key": "ZGV2LW9ubHktZGV2LW9ubHktZGV2LW9ubHktZGV2LTA="},
    {"database_url": "postgresql+asyncpg://stllpay_app:devapp@localhost/x"},
    {"web_origin": "http://pay.stllhaus.co"},
    {"payments_provider": "akahu", "akahu_app_token": None},
    {"payments_provider": "mock"},
    {"live_payments_enabled": True},
])
def test_unsafe_production_config_rejected(override):
    base = {**PROD, "payments_provider": "akahu", "akahu_app_token": "t", "akahu_app_secret": "s"}
    if override.get("live_payments_enabled"):
        base["payments_provider"] = "mock"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**base, **override})


def test_live_payments_never_outside_production():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="staging", payments_provider="akahu",
                 akahu_app_token="t", akahu_app_secret="s", live_payments_enabled=True,
                 **{k: v for k, v in PROD.items() if k != "environment"})


def test_live_payments_allowed_only_when_fully_configured():
    s = Settings(_env_file=None, payments_provider="akahu", akahu_app_token="t",
                 akahu_app_secret="s", live_payments_enabled=True, **PROD)
    assert s.live_payments_enabled is True


def test_password_policy():
    assert security.password_problems("Correct-Horse-42-battery", "a@example.com") == []
    assert security.password_problems("short", "a@example.com")
    assert security.password_problems("alllowercaseletters", "a@example.com")
    assert security.password_problems("Marama-2026-secret", "marama@example.com")


def test_password_hash_roundtrip():
    h = security.hash_password("Correct-Horse-42-battery")
    assert h.startswith("$argon2id$")
    assert security.verify_password(h, "Correct-Horse-42-battery")
    assert not security.verify_password(h, "wrong")
    assert not security.verify_password(None, "anything")


def test_totp_drift_and_replay():
    secret = pyotp.random_base32()
    now = 1_800_000_000.0
    code_prev = pyotp.TOTP(secret).at(now - 30)
    step = security.verify_totp(secret, code_prev, None, now=now)
    assert step is not None
    assert security.verify_totp(secret, code_prev, step, now=now) is None
    assert security.verify_totp(secret, pyotp.TOTP(secret).at(now - 90), None, now=now) is None
    assert security.verify_totp(secret, "12ab56", None, now=now) is None


def test_field_encryption_roundtrip():
    enc = security.encrypt_field("JBSWY3DPEHPK3PXP")
    assert "JBSWY3DPEHPK3PXP" not in enc
    assert security.decrypt_field(enc) == "JBSWY3DPEHPK3PXP"


def test_ids():
    a, b = uuid7(), uuid7()
    assert a.version == 7 and a != b
    code = random_code(8)
    assert len(code) == 8 and not set(code) & set("ILOU")


def test_empty_env_values_mean_unset():
    s = Settings(_env_file=None, app_encryption_key="", akahu_app_token="", akahu_app_secret="")
    assert s.akahu_app_token is None and s.akahu_app_secret is None
    assert s.app_encryption_key.get_secret_value()
    with pytest.raises(ValidationError):
        Settings(_env_file=None, payments_provider="akahu", akahu_app_token="t",
                 akahu_app_secret="s", **{**PROD, "app_encryption_key": ""})


def test_forwarded_origin_trust_is_development_only():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, payments_provider="akahu", akahu_app_token="t",
                 akahu_app_secret="s", dev_trust_forwarded_origin=True, **PROD)


def test_forwarded_origin_check():
    from types import SimpleNamespace

    from app.auth import deps

    def req(origin, host):
        return SimpleNamespace(headers={"origin": origin, "x-forwarded-host": host,
                                        "x-forwarded-proto": "https"})

    strict = Settings(_env_file=None)
    relaxed = Settings(_env_file=None, dev_trust_forwarded_origin=True)
    preview = req("https://preview.example", "preview.example")
    evil = req("https://evil.example", "preview.example")
    orig = deps.get_settings
    try:
        deps.get_settings = lambda: strict
        assert not deps.origin_allowed(preview)
        deps.get_settings = lambda: relaxed
        assert deps.origin_allowed(preview)
        assert not deps.origin_allowed(evil)
    finally:
        deps.get_settings = orig
