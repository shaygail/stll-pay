"""Payments: bank accounts, QR codes, links, requests, attempts, provider events,
reconciliations, POS integrations, API keys, outbound webhooks, and the mock provider.

Revision ID: 0002_payments
Revises: 0001_foundation
Create Date: 2026-11-07
"""

from __future__ import annotations

from alembic import context, op

revision = "0002_payments"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def _app_role() -> str:
    role = context.config.get_main_option("app_db_role") or "stllpay_app"
    if not role.replace("_", "").isalnum():
        raise ValueError("invalid app_db_role")
    return role


UPGRADE_SQL = r"""
-- Runs a lookup that must ignore tenant scoping (for example "which merchant owns this
-- checkout token?") and then restores the caller's context. Only used by the narrow
-- SECURITY DEFINER lookup functions below, each returning ids only.
CREATE FUNCTION app_with_system_context(enable boolean) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE previous text := current_setting('app.is_platform_admin', true);
BEGIN
    PERFORM set_config('app.is_platform_admin', CASE WHEN enable THEN 'true' ELSE 'false' END, true);
    RETURN COALESCE(previous, '');
END $$;

-- ---------------------------------------------------------------- bank accounts
CREATE TABLE merchant_bank_accounts (
    id                  uuid PRIMARY KEY,
    merchant_id         uuid NOT NULL REFERENCES merchants(id),
    account_number_enc  text NOT NULL,
    account_last4       text NOT NULL CHECK (account_last4 ~ '^[0-9]{2,4}$'),
    holder_name         text NOT NULL CHECK (char_length(holder_name) BETWEEN 1 AND 100),
    status              text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'verified', 'rejected', 'retired')),
    created_by          uuid REFERENCES users(id),
    created_at          timestamptz NOT NULL DEFAULT now(),
    verified_by         uuid REFERENCES users(id),
    verified_at         timestamptz,
    retired_at          timestamptz
);
CREATE UNIQUE INDEX merchant_bank_accounts_one_verified
    ON merchant_bank_accounts(merchant_id) WHERE status = 'verified';

-- Account details never change after creation; a new account is a new row.
-- Only a platform admin can verify; new rows always start pending.
CREATE FUNCTION bank_accounts_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'pending' AND NOT app_is_platform_admin() THEN
            RAISE EXCEPTION 'new bank accounts start as pending' USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.account_number_enc IS DISTINCT FROM OLD.account_number_enc
       OR NEW.holder_name IS DISTINCT FROM OLD.holder_name
       OR NEW.merchant_id IS DISTINCT FROM OLD.merchant_id
       OR NEW.account_last4 IS DISTINCT FROM OLD.account_last4 THEN
        RAISE EXCEPTION 'bank account details are immutable' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NEW.status IN ('verified', 'rejected')
       AND NOT app_is_platform_admin() THEN
        RAISE EXCEPTION 'only a platform admin can verify bank accounts'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER bank_accounts_guard BEFORE INSERT OR UPDATE ON merchant_bank_accounts
    FOR EACH ROW EXECUTE FUNCTION bank_accounts_guard();

-- ---------------------------------------------------------------- integrations and keys
CREATE TABLE pos_integrations (
    id           uuid PRIMARY KEY,
    merchant_id  uuid NOT NULL REFERENCES merchants(id),
    name         text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    enabled      boolean NOT NULL DEFAULT true,
    created_by   uuid REFERENCES users(id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER pos_integrations_updated_at BEFORE UPDATE ON pos_integrations
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE api_keys (
    id                  uuid PRIMARY KEY,
    merchant_id         uuid NOT NULL REFERENCES merchants(id),
    pos_integration_id  uuid NOT NULL REFERENCES pos_integrations(id),
    name                text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    prefix              text NOT NULL UNIQUE,
    key_hash            bytea NOT NULL,
    created_by          uuid REFERENCES users(id),
    created_at          timestamptz NOT NULL DEFAULT now(),
    last_used_at        timestamptz,
    expires_at          timestamptz,
    revoked_at          timestamptz
);

CREATE TABLE webhook_endpoints (
    id                  uuid PRIMARY KEY,
    merchant_id         uuid NOT NULL REFERENCES merchants(id),
    pos_integration_id  uuid NOT NULL REFERENCES pos_integrations(id),
    url                 text NOT NULL CHECK (char_length(url) <= 500),
    secret_enc          text NOT NULL,
    enabled             boolean NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- QR codes and links
CREATE TABLE static_qr_codes (
    id           uuid PRIMARY KEY,
    merchant_id  uuid NOT NULL REFERENCES merchants(id),
    code         text NOT NULL UNIQUE CHECK (code ~ '^[0-9A-Z]{8}$'),
    label        text NOT NULL CHECK (char_length(label) BETWEEN 1 AND 80),
    active       boolean NOT NULL DEFAULT true,
    created_by   uuid REFERENCES users(id),
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE payment_links (
    id            uuid PRIMARY KEY,
    merchant_id   uuid NOT NULL REFERENCES merchants(id),
    code          text NOT NULL UNIQUE CHECK (code ~ '^[0-9A-Z]{14}$'),
    amount_cents  bigint CHECK (amount_cents IS NULL OR amount_cents > 0),
    description   text NOT NULL CHECK (char_length(description) BETWEEN 1 AND 140),
    order_ref     text CHECK (char_length(order_ref) <= 60),
    expires_at    timestamptz,
    max_uses      integer CHECK (max_uses IS NULL OR max_uses > 0),
    paid_count    integer NOT NULL DEFAULT 0 CHECK (paid_count >= 0),
    active        boolean NOT NULL DEFAULT true,
    created_by    uuid REFERENCES users(id),
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- payment requests
CREATE TABLE payment_requests (
    id                  uuid PRIMARY KEY,
    merchant_id         uuid NOT NULL REFERENCES merchants(id),
    checkout_token      text NOT NULL UNIQUE CHECK (char_length(checkout_token) >= 22),
    source              text NOT NULL CHECK (source IN ('static_qr', 'dynamic_qr', 'link', 'dashboard')),
    static_qr_id        uuid REFERENCES static_qr_codes(id),
    payment_link_id     uuid REFERENCES payment_links(id),
    pos_integration_id  uuid REFERENCES pos_integrations(id),
    external_order_id   text CHECK (char_length(external_order_id) <= 100),
    idempotency_key     text CHECK (char_length(idempotency_key) <= 200),
    request_hash        bytea,
    amount_cents        bigint NOT NULL CHECK (amount_cents > 0),
    currency            text NOT NULL DEFAULT 'NZD' CHECK (currency = 'NZD'),
    description         text CHECK (char_length(description) <= 140),
    reference           text NOT NULL UNIQUE CHECK (reference ~ '^SP[0-9A-Z]{8}$'),
    status              text NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'paid', 'cancelled', 'expired', 'requires_review')),
    status_reason       text,
    expires_at          timestamptz NOT NULL,
    paid_attempt_id     uuid,
    paid_at             timestamptz,
    created_by          uuid REFERENCES users(id),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX payment_requests_idempotency
    ON payment_requests(merchant_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
-- One live request per POS order: a new one only after the previous was cancelled/expired.
CREATE UNIQUE INDEX payment_requests_one_per_order
    ON payment_requests(merchant_id, pos_integration_id, external_order_id)
    WHERE external_order_id IS NOT NULL AND status IN ('open', 'paid', 'requires_review');
CREATE INDEX payment_requests_merchant_created ON payment_requests(merchant_id, created_at DESC);
CREATE INDEX payment_requests_open_expiry ON payment_requests(expires_at) WHERE status = 'open';
CREATE TRIGGER payment_requests_updated_at BEFORE UPDATE ON payment_requests
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE FUNCTION payment_requests_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.amount_cents IS DISTINCT FROM OLD.amount_cents
       OR NEW.merchant_id IS DISTINCT FROM OLD.merchant_id
       OR NEW.reference IS DISTINCT FROM OLD.reference
       OR NEW.checkout_token IS DISTINCT FROM OLD.checkout_token THEN
        RAISE EXCEPTION 'payment request amount, merchant and reference are immutable'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status IN ('paid', 'cancelled', 'expired') AND NEW.status <> OLD.status
       AND NEW.status <> 'requires_review' THEN
        RAISE EXCEPTION 'payment request % is final', OLD.status USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER payment_requests_guard BEFORE UPDATE ON payment_requests
    FOR EACH ROW EXECUTE FUNCTION payment_requests_guard();

-- ---------------------------------------------------------------- payment attempts
CREATE TABLE payment_attempts (
    id                       uuid PRIMARY KEY,
    merchant_id              uuid NOT NULL REFERENCES merchants(id),
    payment_request_id       uuid NOT NULL REFERENCES payment_requests(id),
    provider                 text NOT NULL CHECK (provider IN ('mock', 'akahu')),
    live                     boolean NOT NULL DEFAULT false,
    provider_payment_id      text UNIQUE,
    amount_cents             bigint NOT NULL CHECK (amount_cents > 0),
    reference                text NOT NULL,
    -- Immutable snapshot of where the money goes.
    destination_account_id   uuid NOT NULL REFERENCES merchant_bank_accounts(id),
    destination_name         text NOT NULL,
    destination_account_enc  text NOT NULL,
    destination_last4        text NOT NULL,
    status                   text NOT NULL DEFAULT 'CREATED' CHECK (status IN (
        'CREATED', 'AWAITING_AUTHORISATION', 'AUTHORISED', 'PROCESSING', 'SENT',
        'SETTLED_OR_RECONCILED', 'FAILED', 'CANCELLED', 'EXPIRED', 'REQUIRES_REVIEW')),
    status_reason            text,
    provider_status          text,
    provider_status_code     text,
    authorisation_url        text,
    provider_expires_at      timestamptz,
    last_event_at            timestamptz,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    authorised_at            timestamptz,
    sent_at                  timestamptz,
    terminal_at              timestamptz,
    last_polled_at           timestamptz,
    next_poll_at             timestamptz,
    poll_count               integer NOT NULL DEFAULT 0,
    version                  integer NOT NULL DEFAULT 0
);
-- Never two successful payments for one request.
CREATE UNIQUE INDEX payment_attempts_one_success ON payment_attempts(payment_request_id)
    WHERE status IN ('SENT', 'SETTLED_OR_RECONCILED');
-- Never two attempts in flight for one request (stops double charging on double taps).
CREATE UNIQUE INDEX payment_attempts_one_in_flight ON payment_attempts(payment_request_id)
    WHERE status IN ('CREATED', 'AWAITING_AUTHORISATION', 'AUTHORISED', 'PROCESSING');
CREATE INDEX payment_attempts_poll_due ON payment_attempts(next_poll_at)
    WHERE next_poll_at IS NOT NULL;
CREATE INDEX payment_attempts_request ON payment_attempts(payment_request_id, created_at);
CREATE TRIGGER payment_attempts_updated_at BEFORE UPDATE ON payment_attempts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE FUNCTION payment_attempts_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.amount_cents IS DISTINCT FROM OLD.amount_cents
       OR NEW.destination_account_id IS DISTINCT FROM OLD.destination_account_id
       OR NEW.destination_name IS DISTINCT FROM OLD.destination_name
       OR NEW.destination_account_enc IS DISTINCT FROM OLD.destination_account_enc
       OR NEW.merchant_id IS DISTINCT FROM OLD.merchant_id
       OR NEW.payment_request_id IS DISTINCT FROM OLD.payment_request_id
       OR NEW.reference IS DISTINCT FROM OLD.reference
       OR NEW.provider IS DISTINCT FROM OLD.provider
       OR NEW.live IS DISTINCT FROM OLD.live
       OR (OLD.provider_payment_id IS NOT NULL
           AND NEW.provider_payment_id IS DISTINCT FROM OLD.provider_payment_id) THEN
        RAISE EXCEPTION 'payment attempt amount and destination are immutable'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status IN ('SETTLED_OR_RECONCILED', 'FAILED', 'CANCELLED', 'EXPIRED')
       AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'payment attempt % is final', OLD.status USING ERRCODE = 'check_violation';
    END IF;
    NEW.version := OLD.version + 1;
    RETURN NEW;
END $$;
CREATE TRIGGER payment_attempts_guard BEFORE UPDATE ON payment_attempts
    FOR EACH ROW EXECUTE FUNCTION payment_attempts_guard();

-- ---------------------------------------------------------------- provider events
CREATE TABLE payment_provider_events (
    id                   uuid PRIMARY KEY,
    provider             text NOT NULL,
    merchant_id          uuid REFERENCES merchants(id),
    attempt_id           uuid REFERENCES payment_attempts(id),
    provider_payment_id  text,
    dedup_key            bytea NOT NULL UNIQUE,
    signature_valid      boolean NOT NULL,
    status               text,
    status_code          text,
    emitted_at           timestamptz,
    received_at          timestamptz NOT NULL DEFAULT now(),
    raw_body             text NOT NULL CHECK (octet_length(raw_body) <= 65536),
    processed_at         timestamptz,
    outcome              text
);
CREATE INDEX payment_provider_events_attempt ON payment_provider_events(attempt_id);

CREATE TABLE payment_reconciliations (
    id               uuid PRIMARY KEY,
    merchant_id      uuid NOT NULL REFERENCES merchants(id),
    attempt_id       uuid NOT NULL REFERENCES payment_attempts(id),
    method           text NOT NULL CHECK (method IN ('webhook', 'provider_poll', 'checkout_refresh',
                                                     'admin_resolution', 'expiry')),
    from_status      text NOT NULL,
    to_status        text NOT NULL,
    provider_status  text,
    provider_code    text,
    operator_id      uuid REFERENCES users(id),
    note             text,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX payment_reconciliations_attempt ON payment_reconciliations(attempt_id, created_at);

-- ---------------------------------------------------------------- outbound webhooks
CREATE TABLE webhook_deliveries (
    id                uuid PRIMARY KEY,
    merchant_id       uuid NOT NULL REFERENCES merchants(id),
    endpoint_id       uuid NOT NULL REFERENCES webhook_endpoints(id),
    event_id          uuid NOT NULL,
    event_type        text NOT NULL,
    payload           jsonb NOT NULL,
    status            text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'delivered', 'failed')),
    attempts          integer NOT NULL DEFAULT 0,
    next_attempt_at   timestamptz NOT NULL DEFAULT now(),
    last_status_code  integer,
    last_error        text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    delivered_at      timestamptz,
    UNIQUE (endpoint_id, event_id)
);
CREATE INDEX webhook_deliveries_due ON webhook_deliveries(next_attempt_at) WHERE status = 'pending';

-- One event per request outcome, so a POS never gets "paid" twice for one order.
CREATE TABLE payment_events (
    id                  uuid PRIMARY KEY,
    merchant_id         uuid NOT NULL REFERENCES merchants(id),
    payment_request_id  uuid NOT NULL REFERENCES payment_requests(id),
    event_type          text NOT NULL,
    payload             jsonb NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (payment_request_id, event_type)
);

-- ---------------------------------------------------------------- row-level security
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['merchant_bank_accounts', 'pos_integrations', 'api_keys',
        'webhook_endpoints', 'static_qr_codes', 'payment_links', 'payment_requests',
        'payment_attempts', 'payment_reconciliations', 'webhook_deliveries', 'payment_events']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY %I ON %I FOR ALL
            USING (app_is_platform_admin() OR merchant_id = app_current_merchant_id())
            WITH CHECK (app_is_platform_admin() OR merchant_id = app_current_merchant_id())',
            t || '_tenant', t);
    END LOOP;
END $$;

ALTER TABLE payment_provider_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE payment_provider_events FORCE ROW LEVEL SECURITY;
CREATE POLICY payment_provider_events_read ON payment_provider_events FOR SELECT USING (
    app_is_platform_admin() OR (merchant_id IS NOT NULL AND merchant_id = app_current_merchant_id()));
CREATE POLICY payment_provider_events_insert ON payment_provider_events FOR INSERT WITH CHECK (true);
CREATE POLICY payment_provider_events_update ON payment_provider_events FOR UPDATE
    USING (app_is_platform_admin()) WITH CHECK (app_is_platform_admin());

-- ---------------------------------------------------------------- narrow lookups
-- Each resolves a public identifier to a merchant id, ignoring tenant scoping, and
-- returns nothing else. Callers then set app.merchant_id and query normally.
CREATE FUNCTION lookup_merchant_by_slug(p_slug text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE prev text := app_with_system_context(true); result uuid;
BEGIN
    SELECT id INTO result FROM merchants
     WHERE slug = p_slug AND deleted_at IS NULL AND status IN ('restricted', 'active');
    PERFORM set_config('app.is_platform_admin', prev, true);
    RETURN result;
END $$;

CREATE FUNCTION lookup_request_by_token(p_token text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE prev text := app_with_system_context(true); result uuid;
BEGIN
    SELECT merchant_id INTO result FROM payment_requests WHERE checkout_token = p_token;
    PERFORM set_config('app.is_platform_admin', prev, true);
    RETURN result;
END $$;

CREATE FUNCTION lookup_link_by_code(p_code text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE prev text := app_with_system_context(true); result uuid;
BEGIN
    SELECT merchant_id INTO result FROM payment_links WHERE code = p_code;
    PERFORM set_config('app.is_platform_admin', prev, true);
    RETURN result;
END $$;

CREATE FUNCTION lookup_api_key(p_prefix text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE prev text := app_with_system_context(true); result uuid;
BEGIN
    SELECT merchant_id INTO result FROM api_keys WHERE prefix = p_prefix;
    PERFORM set_config('app.is_platform_admin', prev, true);
    RETURN result;
END $$;

CREATE FUNCTION lookup_attempt_by_provider_id(p_provider text, p_id text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE prev text := app_with_system_context(true); result uuid;
BEGIN
    SELECT merchant_id INTO result FROM payment_attempts
     WHERE provider = p_provider AND provider_payment_id = p_id;
    PERFORM set_config('app.is_platform_admin', prev, true);
    RETURN result;
END $$;

REVOKE ALL ON FUNCTION app_with_system_context(boolean) FROM PUBLIC;

-- ---------------------------------------------------------------- mock provider
-- Simulates an external payment provider for development and tests. Kept in its own
-- schema; the API refuses to use it in production.
CREATE SCHEMA mock_provider;
CREATE TABLE mock_provider.keys (
    id           text PRIMARY KEY,
    private_pem  text NOT NULL,
    public_pem   text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE mock_provider.payments (
    id                text PRIMARY KEY,
    amount_cents      bigint NOT NULL,
    payee_name        text NOT NULL,
    payee_account     text NOT NULL,
    payee_reference   text,
    redirect_uri      text NOT NULL,
    webhook_uri       text,
    status            text NOT NULL,
    status_code       text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    expires_at        timestamptz NOT NULL,
    authorised_at     timestamptz,
    settle_at         timestamptz,
    final_status      text,
    final_code        text,
    suppress_webhooks boolean NOT NULL DEFAULT false
);
CREATE TABLE mock_provider.webhooks (
    id            bigserial PRIMARY KEY,
    payment_id    text NOT NULL REFERENCES mock_provider.payments(id),
    target        text NOT NULL,
    body          text NOT NULL,
    signature     text NOT NULL,
    key_id        text NOT NULL,
    attempts      integer NOT NULL DEFAULT 0,
    next_at       timestamptz NOT NULL DEFAULT now(),
    delivered_at  timestamptz
);

-- ---------------------------------------------------------------- grants
GRANT SELECT, INSERT, UPDATE ON merchant_bank_accounts, pos_integrations, api_keys,
    webhook_endpoints, static_qr_codes, payment_links, payment_requests, payment_attempts,
    payment_provider_events, webhook_deliveries TO __APP_ROLE__;
GRANT SELECT, INSERT ON payment_reconciliations, payment_events TO __APP_ROLE__;
GRANT USAGE ON SCHEMA mock_provider TO __APP_ROLE__;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA mock_provider TO __APP_ROLE__;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA mock_provider TO __APP_ROLE__;
"""

DOWNGRADE_SQL = r"""
DROP SCHEMA IF EXISTS mock_provider CASCADE;
DROP FUNCTION IF EXISTS lookup_attempt_by_provider_id(text, text), lookup_api_key(text),
    lookup_link_by_code(text), lookup_request_by_token(text), lookup_merchant_by_slug(text);
DROP TABLE IF EXISTS payment_events, webhook_deliveries, payment_reconciliations,
    payment_provider_events, payment_attempts, payment_requests, payment_links,
    static_qr_codes, webhook_endpoints, api_keys, pos_integrations,
    merchant_bank_accounts CASCADE;
DROP FUNCTION IF EXISTS payment_attempts_guard(), payment_requests_guard(),
    bank_accounts_guard(), app_with_system_context(boolean);
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL.replace("__APP_ROLE__", _app_role()))


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
