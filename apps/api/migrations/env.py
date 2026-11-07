"""Alembic environment. Migrations run as the schema owner (MIGRATION_DATABASE_URL)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from app.core.config import get_settings

config = context.config
settings = get_settings()
config.set_main_option("app_db_role", settings.app_db_role)


def run_migrations_online() -> None:
    engine = create_engine(settings.migration_database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported; run against a database.")
run_migrations_online()
