"""Alembic migrations use the same environment-driven Postgres connection as the app."""
from alembic import context
from sqlalchemy import pool

from postgres_app.db import database_url
from postgres_app.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    from sqlalchemy import create_engine

    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


def run_migrations_offline() -> None:
    # The database password is never printed; offline generation is not enabled.
    raise RuntimeError("Offline migrations are intentionally disabled for this sample")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
