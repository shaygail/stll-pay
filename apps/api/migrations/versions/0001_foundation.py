"""Foundation: users, sessions, merchants, memberships, audit log, row-level security.

Revision ID: 0001_foundation
Revises:
Create Date: 2026-11-07
"""

from __future__ import annotations

from alembic import context, op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def _app_role() -> str:
    role = context.config.get_main_option("app_db_role") or "stllpay_app"
    if not role.replace("_", "").isalnum():
        raise ValueError("invalid app_db_role")
    return role


UPGRADE_SQL = r"""
CREATE EXTENSION IF NOT EXISTS citext;

-- Tenant context helpers. Values are set per transaction with set_config(..., true).
CREATE FUNCTION app_current_user_id() RETURNS uuid LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$;
CREATE FUNCTION app_current_merchant_id() RETURNS uuid LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('app.merchant_id', true), '')::uuid $$;
CREATE FUNCTION app_is_platform_admin() RETURNS boolean LANGUAGE sql STABLE AS
$$ SELECT COALESCE(NULLIF(current_setting('app.is_platform_admin', true), '')::boolean, false) $$;

CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS
$$ BEGIN NEW.updated_at := now(); RETURN NEW; END $$;

-- ---------------------------------------------------------------- users
CREATE TABLE users (
    id                  uuid PRIMARY KEY,
    email               citext NOT NULL UNIQUE,
    password_hash       text NOT NULL,
    display_name        text,
    email_verified_at   timestamptz,
    mfa_secret_enc      text,
    mfa_enabled_at      timestamptz,
    mfa_last_step       bigint,
    is_platform_admin   boolean NOT NULL DEFAULT false,
    failed_login_count  integer NOT NULL DEFAULT 0 CHECK (failed_login_count >= 0),
    locked_until        timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,
    CONSTRAINT users_email_len CHECK (char_length(email) <= 254)
);
CREATE TRIGGER users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Platform admin can only be granted by the schema owner (a manual, audited operation),
-- never through the application role.
CREATE FUNCTION users_guard_admin_flag() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF current_user = '__APP_ROLE__' AND
       ((TG_OP = 'INSERT' AND NEW.is_platform_admin) OR
        (TG_OP = 'UPDATE' AND NEW.is_platform_admin IS DISTINCT FROM OLD.is_platform_admin)) THEN
        RAISE EXCEPTION 'is_platform_admin cannot be changed by the application'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER users_admin_flag BEFORE INSERT OR UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION users_guard_admin_flag();

-- ---------------------------------------------------------------- sessions
CREATE TABLE user_sessions (
    id               uuid PRIMARY KEY,
    user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash       bytea NOT NULL UNIQUE,
    created_at       timestamptz NOT NULL DEFAULT now(),
    last_seen_at     timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz NOT NULL,
    mfa_verified_at  timestamptz,
    revoked_at       timestamptz,
    ip               inet,
    user_agent       text
);
CREATE INDEX user_sessions_user_idx ON user_sessions(user_id) WHERE revoked_at IS NULL;

CREATE TABLE user_tokens (
    id          uuid PRIMARY KEY,
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose     text NOT NULL CHECK (purpose IN ('verify_email', 'reset_password')),
    token_hash  bytea NOT NULL UNIQUE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    used_at     timestamptz
);
CREATE INDEX user_tokens_user_idx ON user_tokens(user_id, purpose);

-- ---------------------------------------------------------------- merchants
CREATE TABLE merchants (
    id             uuid PRIMARY KEY,
    slug           citext NOT NULL UNIQUE
                   CHECK (slug ~ '^[a-z0-9](?:[a-z0-9-]{1,46}[a-z0-9])$'),
    legal_name     text NOT NULL CHECK (char_length(legal_name) BETWEEN 1 AND 200),
    trading_name   text NOT NULL CHECK (char_length(trading_name) BETWEEN 1 AND 120),
    nzbn           text CHECK (nzbn ~ '^94[0-9]{11}$'),
    contact_email  citext,
    contact_phone  text,
    status         text NOT NULL DEFAULT 'draft'
                   CHECK (status IN ('draft', 'pending_review', 'restricted', 'active', 'suspended')),
    live_enabled   boolean NOT NULL DEFAULT false,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    deleted_at     timestamptz,
    -- Live payments are only possible for an active merchant.
    CONSTRAINT merchants_live_needs_active CHECK (NOT live_enabled OR status = 'active')
);
CREATE TRIGGER merchants_updated_at BEFORE UPDATE ON merchants
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Only platform admins may change approval state or live payments, and new merchants
-- always start as draft with live payments off.
CREATE FUNCTION merchants_guard_admin_fields() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF app_is_platform_admin() THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' OR NEW.live_enabled THEN
            RAISE EXCEPTION 'new merchants start as draft with live payments off'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    ELSIF NEW.live_enabled IS DISTINCT FROM OLD.live_enabled THEN
        RAISE EXCEPTION 'only a platform admin can change live payments'
            USING ERRCODE = 'insufficient_privilege';
    ELSIF NEW.status IS DISTINCT FROM OLD.status
          AND NOT (OLD.status = 'draft' AND NEW.status = 'pending_review') THEN
        RAISE EXCEPTION 'only a platform admin can change merchant status'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER merchants_admin_fields BEFORE INSERT OR UPDATE ON merchants
    FOR EACH ROW EXECUTE FUNCTION merchants_guard_admin_fields();

CREATE TABLE merchant_memberships (
    id           uuid PRIMARY KEY,
    merchant_id  uuid NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role         text NOT NULL CHECK (role IN ('owner', 'manager', 'cashier', 'viewer')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (merchant_id, user_id)
);
CREATE INDEX merchant_memberships_user_idx ON merchant_memberships(user_id);
CREATE TRIGGER merchant_memberships_updated_at BEFORE UPDATE ON merchant_memberships
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- A merchant always keeps at least one owner.
CREATE FUNCTION memberships_keep_owner() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE remaining integer;
BEGIN
    IF OLD.role = 'owner' AND (TG_OP = 'DELETE' OR NEW.role <> 'owner') THEN
        SELECT count(*) INTO remaining FROM merchant_memberships
         WHERE merchant_id = OLD.merchant_id AND role = 'owner' AND id <> OLD.id;
        IF remaining = 0 AND EXISTS (SELECT 1 FROM merchants WHERE id = OLD.merchant_id) THEN
            RAISE EXCEPTION 'a merchant must keep at least one owner'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER memberships_keep_owner BEFORE UPDATE OR DELETE ON merchant_memberships
    FOR EACH ROW EXECUTE FUNCTION memberships_keep_owner();

-- Membership lookup that ignores RLS, used only inside policies (avoids policy recursion).
CREATE FUNCTION app_user_is_member(m uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$$ SELECT EXISTS (SELECT 1 FROM merchant_memberships
                  WHERE merchant_id = m AND user_id = app_current_user_id()) $$;

-- ---------------------------------------------------------------- audit log
CREATE TABLE audit_logs (
    id             bigserial PRIMARY KEY,
    created_at     timestamptz NOT NULL DEFAULT now(),
    actor_user_id  uuid,
    merchant_id    uuid,
    action         text NOT NULL CHECK (char_length(action) BETWEEN 1 AND 100),
    target_type    text,
    target_id      text,
    ip             inet,
    metadata       jsonb NOT NULL DEFAULT '{}'::jsonb,
    prev_hash      bytea NOT NULL,
    hash           bytea NOT NULL
);
CREATE INDEX audit_logs_merchant_idx ON audit_logs(merchant_id, id DESC);
CREATE INDEX audit_logs_actor_idx ON audit_logs(actor_user_id, id DESC);

-- Single-row chain head. Not reachable by the app role; only the trigger function uses it.
CREATE TABLE audit_chain_head (
    id         boolean PRIMARY KEY DEFAULT true CHECK (id),
    last_hash  bytea NOT NULL
);
INSERT INTO audit_chain_head (last_hash) VALUES ('\x00'::bytea);

CREATE FUNCTION audit_chain() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE prev bytea;
BEGIN
    SELECT last_hash INTO prev FROM audit_chain_head WHERE id FOR UPDATE;
    NEW.created_at := now();
    NEW.prev_hash := prev;
    NEW.hash := sha256(prev || convert_to(concat_ws('|',
        NEW.id::text, to_char(NEW.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'), COALESCE(NEW.actor_user_id::text, ''),
        COALESCE(NEW.merchant_id::text, ''), NEW.action, COALESCE(NEW.target_type, ''),
        COALESCE(NEW.target_id, ''), COALESCE(host(NEW.ip), ''), NEW.metadata::text), 'UTF8'));
    UPDATE audit_chain_head SET last_hash = NEW.hash WHERE id;
    RETURN NEW;
END $$;
CREATE TRIGGER audit_logs_chain BEFORE INSERT ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_chain();

CREATE FUNCTION audit_logs_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only' USING ERRCODE = 'insufficient_privilege';
END $$;
CREATE TRIGGER audit_logs_no_update BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_logs_immutable();
CREATE TRIGGER audit_logs_no_truncate BEFORE TRUNCATE ON audit_logs
    FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_immutable();

-- Recomputes the chain; returns the id of the first broken row, or NULL if intact.
CREATE FUNCTION audit_verify_chain() RETURNS bigint
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE r audit_logs; prev bytea := '\x00'::bytea; expected bytea;
BEGIN
    IF NOT app_is_platform_admin() THEN
        RAISE EXCEPTION 'only platform admins can verify the audit chain'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    FOR r IN SELECT * FROM audit_logs ORDER BY id LOOP
        expected := sha256(prev || convert_to(concat_ws('|',
            r.id::text, to_char(r.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'), COALESCE(r.actor_user_id::text, ''),
            COALESCE(r.merchant_id::text, ''), r.action, COALESCE(r.target_type, ''),
            COALESCE(r.target_id, ''), COALESCE(host(r.ip), ''), r.metadata::text), 'UTF8'));
        IF r.prev_hash <> prev OR r.hash <> expected THEN
            RETURN r.id;
        END IF;
        prev := r.hash;
    END LOOP;
    RETURN NULL;
END $$;

-- ---------------------------------------------------------------- row-level security
ALTER TABLE merchants ENABLE ROW LEVEL SECURITY;
ALTER TABLE merchants FORCE ROW LEVEL SECURITY;
CREATE POLICY merchants_select ON merchants FOR SELECT USING (
    app_is_platform_admin() OR id = app_current_merchant_id() OR app_user_is_member(id));
CREATE POLICY merchants_insert ON merchants FOR INSERT WITH CHECK (
    app_current_user_id() IS NOT NULL AND id = app_current_merchant_id());
CREATE POLICY merchants_update ON merchants FOR UPDATE
    USING (app_is_platform_admin() OR id = app_current_merchant_id())
    WITH CHECK (app_is_platform_admin() OR id = app_current_merchant_id());

ALTER TABLE merchant_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE merchant_memberships FORCE ROW LEVEL SECURITY;
CREATE POLICY memberships_select ON merchant_memberships FOR SELECT USING (
    app_is_platform_admin() OR merchant_id = app_current_merchant_id()
    OR user_id = app_current_user_id());
CREATE POLICY memberships_write ON merchant_memberships FOR ALL
    USING (app_is_platform_admin() OR merchant_id = app_current_merchant_id())
    WITH CHECK (app_is_platform_admin() OR merchant_id = app_current_merchant_id());

ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY;
CREATE POLICY audit_select ON audit_logs FOR SELECT USING (
    app_is_platform_admin()
    OR (merchant_id IS NOT NULL AND merchant_id = app_current_merchant_id()));
CREATE POLICY audit_insert ON audit_logs FOR INSERT WITH CHECK (
    merchant_id IS NULL OR app_is_platform_admin() OR merchant_id = app_current_merchant_id());

-- ---------------------------------------------------------------- grants
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON users, user_sessions, user_tokens TO __APP_ROLE__;
GRANT SELECT, INSERT, UPDATE, DELETE ON merchants, merchant_memberships TO __APP_ROLE__;
GRANT SELECT, INSERT ON audit_logs TO __APP_ROLE__;
GRANT USAGE ON SEQUENCE audit_logs_id_seq TO __APP_ROLE__;
REVOKE ALL ON audit_chain_head FROM __APP_ROLE__;
REVOKE ALL ON FUNCTION audit_verify_chain() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION audit_verify_chain() TO __APP_ROLE__;
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS audit_chain_head, audit_logs, merchant_memberships, merchants,
    user_tokens, user_sessions, users CASCADE;
DROP FUNCTION IF EXISTS audit_verify_chain(), audit_logs_immutable(), audit_chain(),
    app_user_is_member(uuid), memberships_keep_owner(), merchants_guard_admin_fields(),
    users_guard_admin_flag(), set_updated_at(), app_is_platform_admin(),
    app_current_merchant_id(), app_current_user_id();
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL.replace("__APP_ROLE__", _app_role()))


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
