"""Platform settings (single-merchant launch flag) and staff invitations.

Revision ID: 0003_platform_settings
Revises: 0002_payments
Create Date: 2026-11-07
"""

from __future__ import annotations

from alembic import context, op

revision = "0003_platform_settings"
down_revision = "0002_payments"
branch_labels = None
depends_on = None


def _app_role() -> str:
    role = context.config.get_main_option("app_db_role") or "stllpay_app"
    if not role.replace("_", "").isalnum():
        raise ValueError("invalid app_db_role")
    return role


UPGRADE_SQL = r"""
-- One row of platform-wide switches. Launch state: STLL HAUS only (all off).
CREATE TABLE platform_settings (
    id                       boolean PRIMARY KEY DEFAULT true CHECK (id),
    multi_merchant_enabled   boolean NOT NULL DEFAULT false,
    updated_at               timestamptz NOT NULL DEFAULT now(),
    updated_by               uuid REFERENCES users(id)
);
INSERT INTO platform_settings DEFAULT VALUES;

-- Only a platform admin may change platform settings.
CREATE FUNCTION platform_settings_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT app_is_platform_admin() THEN
        RAISE EXCEPTION 'only a platform admin can change platform settings'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;
CREATE TRIGGER platform_settings_guard BEFORE UPDATE ON platform_settings
    FOR EACH ROW EXECUTE FUNCTION platform_settings_guard();

-- Staff invitations reuse the single-use token table.
ALTER TABLE user_tokens DROP CONSTRAINT user_tokens_purpose_check;
ALTER TABLE user_tokens ADD CONSTRAINT user_tokens_purpose_check
    CHECK (purpose IN ('verify_email', 'reset_password', 'invite'));

GRANT SELECT, UPDATE ON platform_settings TO __APP_ROLE__;
"""

DOWNGRADE_SQL = r"""
DELETE FROM user_tokens WHERE purpose = 'invite';
ALTER TABLE user_tokens DROP CONSTRAINT user_tokens_purpose_check;
ALTER TABLE user_tokens ADD CONSTRAINT user_tokens_purpose_check
    CHECK (purpose IN ('verify_email', 'reset_password'));
DROP TABLE IF EXISTS platform_settings;
DROP FUNCTION IF EXISTS platform_settings_guard();
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL.replace("__APP_ROLE__", _app_role()))


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
