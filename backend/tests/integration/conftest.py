"""Integration fixtures: a real Postgres 16, migrated with Alembic, behind the real app.

By default a Testcontainers Postgres is started (needs Docker; CI and the host have it, the
devcontainer doesn't). Set `CT_TEST_DATABASE_URL` to an empty, disposable database to use that
instead; every test truncates all tables, and one downgrades to base. `guard.py` refuses any URL
that isn't a loopback host with "test" in the database name.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

from app.api.deps import SESSION_COOKIE, InMemorySessionStore
from app.db.models import Base
from app.enums import Role
from app.main import create_app
from app.schemas import User
from app.settings import Settings
from tests.integration.guard import check_disposable

BACKEND = Path(__file__).resolve().parents[2]
WEBHOOK_TOKEN = "integration-webhook-token"
ADMIN_TOKEN = "integration-admin"


def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = os.environ.get("CT_TEST_DATABASE_URL")
    if url:
        check_disposable(url)
        yield url
        return
    # testcontainers >= 4.15 deprecates `testcontainers.postgres`, and pytest treats warnings
    # as errors.
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="psycopg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    command.upgrade(alembic_config(database_url), "head")
    engine = create_engine(database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def clean_db(engine: Engine) -> Engine:
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    return engine


@pytest.fixture
def app(clean_db: Engine, database_url: str):
    sessions = InMemorySessionStore()
    sessions.put(ADMIN_TOKEN, User(username="admin", role=Role.admin))
    return create_app(
        Settings(database_url=database_url, alert_webhook_token=WEBHOOK_TOKEN),
        sessions=sessions,
    )


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    # The context manager runs the app's lifespan, which disposes its engine.
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, ADMIN_TOKEN)
        yield client
