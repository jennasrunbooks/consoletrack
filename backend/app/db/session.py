"""Engine and unit-of-work factory for a `DATABASE_URL`."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db.repositories import UnitOfWork
from app.db.sql import SqlUnitOfWork


def make_engine(database_url: str) -> Engine:
    # pool_pre_ping: CloudNativePG failovers and restarts drop idle connections.
    return create_engine(database_url, pool_pre_ping=True)


def make_uow_factory(engine: Engine) -> Callable[[], UnitOfWork]:
    sessions = sessionmaker(engine, expire_on_commit=False)
    return lambda: SqlUnitOfWork(sessions())


def make_db_check(engine: Engine) -> Callable[[], None]:
    """Raises if the database can't run `SELECT 1`; used by /readyz."""

    def check() -> None:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    return check
