"""Alembic environment: DATABASE_URL from app settings, metadata from app.db.models."""

from alembic import context
from sqlalchemy import create_engine

from app.db.models import Base
from app.settings import Settings

# `alembic upgrade head --sql` renders the migration without a database; it only needs the
# dialect, so a placeholder URL (no host, no credentials) is enough there.
OFFLINE_URL = "postgresql+psycopg://"

target_metadata = Base.metadata


def database_url() -> str:
    # A caller (tests, scripts) can pass a URL through the Alembic config.
    url = context.config.attributes.get("database_url") or Settings().database_url
    if url:
        return url
    if context.is_offline_mode():
        return OFFLINE_URL
    raise RuntimeError("DATABASE_URL is not set; see .env.example")


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run_online(connection)
        return
    engine = create_engine(database_url())
    with engine.connect() as connection:
        _run_online(connection)
    engine.dispose()


def _run_online(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
