-- Creates the two database roles and the dev, test and e2e databases.
-- stllpay_owner owns the schema and runs migrations.
-- stllpay_app is what the API connects as; row-level security applies to it.
-- Development passwords only.

CREATE ROLE stllpay_owner LOGIN PASSWORD 'devowner';
CREATE ROLE stllpay_app LOGIN PASSWORD 'devapp';

CREATE DATABASE stllpay_dev OWNER stllpay_owner;
CREATE DATABASE stllpay_test OWNER stllpay_owner;
CREATE DATABASE stllpay_e2e OWNER stllpay_owner;

\connect stllpay_dev
CREATE EXTENSION IF NOT EXISTS citext;
GRANT USAGE ON SCHEMA public TO stllpay_app;
GRANT CREATE ON SCHEMA public TO stllpay_owner;

\connect stllpay_test
CREATE EXTENSION IF NOT EXISTS citext;
GRANT USAGE ON SCHEMA public TO stllpay_app;
GRANT CREATE ON SCHEMA public TO stllpay_owner;

\connect stllpay_e2e
CREATE EXTENSION IF NOT EXISTS citext;
GRANT USAGE ON SCHEMA public TO stllpay_app;
GRANT CREATE ON SCHEMA public TO stllpay_owner;
