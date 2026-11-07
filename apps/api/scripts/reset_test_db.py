"""Drop and rebuild a disposable test database (used by end-to-end tests).

Refuses to run unless ENVIRONMENT is "test" and the database name contains
"test" or "e2e", so it can't wipe a real database by mistake.
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402


def main() -> None:
    settings = get_settings()
    name = make_url(settings.migration_database_url).database or ""
    if settings.environment != "test" or not ("test" in name or "e2e" in name):
        raise SystemExit(f"Refusing to reset database {name!r} in {settings.environment!r}")
    engine = create_engine(settings.migration_database_url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS mock_provider CASCADE"))
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {settings.app_db_role}"))
    engine.dispose()
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")
    print(f"Reset {name}")


if __name__ == "__main__":
    main()
