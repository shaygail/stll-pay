"""Runtime settings.

All configuration comes from environment variables. The defaults are safe for local
development only. Staging and production refuse to start without explicit secrets, and
real payments are off unless switched on deliberately (see ``live_payments_enabled``).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]

# Fixed key for development and tests only. Never accepted in staging or production.
_DEV_ENCRYPTION_KEY = "ZGV2LW9ubHktZGV2LW9ubHktZGV2LW9ubHktZGV2LTA="


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    environment: Environment = "development"

    # Database. The app connects as a non-owner role so row-level security applies.
    database_url: str = "postgresql+asyncpg://stllpay_app:devapp@localhost:5432/stllpay_dev"
    # Migrations run as the schema owner.
    migration_database_url: str = (
        "postgresql+psycopg://stllpay_owner:devowner@localhost:5432/stllpay_dev"
    )
    # Name of the role the app connects as. Migrations grant table rights to it.
    app_db_role: str = "stllpay_app"

    # Fernet key (urlsafe base64, 32 bytes) for secrets stored in the database,
    # such as TOTP seeds.
    app_encryption_key: SecretStr = SecretStr(_DEV_ENCRYPTION_KEY)

    # Browser-facing origin of the web app; used for CSRF origin checks and links.
    web_origin: str = "http://localhost:3000"
    # Development only: also accept the origin the browser actually used, as reported by
    # the web app's proxy (x-forwarded-host). For previews behind a forwarding URL.
    dev_trust_forwarded_origin: bool = False

    # Sessions
    session_cookie_name: str = "stllpay_session"
    csrf_cookie_name: str = "stllpay_csrf"
    session_idle_minutes: int = 30
    session_absolute_hours: int = 12
    # Akahu accreditation: MFA status must be renewed every 2 hours before
    # payment-sensitive actions.
    mfa_fresh_minutes: int = 120

    # Login protection
    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    # Email: "console" logs messages, "file" writes them to email_outbox_dir (tests, e2e).
    email_backend: Literal["console", "file"] = "console"
    email_outbox_dir: str = ".outbox"

    # Public base URL of this API, used for provider webhooks. Behind the web app's
    # rewrite this is for example https://pay.stllhaus.co/api
    public_api_url: str = "http://localhost:8000"
    # Largest single payment, in cents (banks apply their own limits too).
    max_payment_cents: int = 1_000_000

    # Payments. Mock by default; Akahu needs explicit configuration.
    payments_provider: Literal["mock", "akahu"] = "mock"
    live_payments_enabled: bool = False
    akahu_api_url: str = "https://api.payments.akahu.io"
    akahu_app_token: SecretStr | None = None
    akahu_app_secret: SecretStr | None = None

    log_level: str = "INFO"
    rate_limit_enabled: bool = True

    trusted_hosts: list[str] = Field(default_factory=lambda: ["*"])

    @field_validator("app_encryption_key", "akahu_app_token", "akahu_app_secret", mode="before")
    @classmethod
    def _empty_is_unset(cls, value: object, info: object) -> object:
        # An empty "KEY=" line (as in .env.example) means "not set".
        if value == "" or (isinstance(value, SecretStr) and value.get_secret_value() == ""):
            field = getattr(info, "field_name", "")
            return _DEV_ENCRYPTION_KEY if field == "app_encryption_key" else None
        return value

    @property
    def is_deployed(self) -> bool:
        return self.environment in ("staging", "production")

    @property
    def secure_cookies(self) -> bool:
        return self.is_deployed

    @model_validator(mode="after")
    def _check_safety(self) -> Settings:
        if self.is_deployed:
            if self.app_encryption_key.get_secret_value() == _DEV_ENCRYPTION_KEY:
                raise ValueError("APP_ENCRYPTION_KEY must be set in staging and production")
            if "devapp" in self.database_url or "devowner" in self.migration_database_url:
                raise ValueError("Development database credentials are not allowed here")
            if self.dev_trust_forwarded_origin:
                raise ValueError("DEV_TRUST_FORWARDED_ORIGIN is for development only")
            if not self.web_origin.startswith("https://"):
                raise ValueError("WEB_ORIGIN must be https in staging and production")
        if self.payments_provider == "akahu" and not (
            self.akahu_app_token and self.akahu_app_secret
        ):
            raise ValueError("PAYMENTS_PROVIDER=akahu needs AKAHU_APP_TOKEN and AKAHU_APP_SECRET")
        if self.environment == "production" and self.payments_provider == "mock":
            raise ValueError("The mock payment provider is not allowed in production")
        if self.live_payments_enabled and self.environment != "production":
            raise ValueError("LIVE_PAYMENTS_ENABLED is only allowed in production")
        if self.live_payments_enabled and self.payments_provider != "akahu":
            raise ValueError("LIVE_PAYMENTS_ENABLED needs PAYMENTS_PROVIDER=akahu")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
