from __future__ import annotations

import pytest

from tests.integration.guard import UnsafeTestDatabase, check_disposable


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://ct@localhost:5432/ct_test",
        "postgresql+psycopg://ct@127.0.0.1:55432/consoletrack_test",
        "postgresql+psycopg://ct@[::1]/TestDB",
    ],
)
def test_accepts_local_test_databases(url: str) -> None:
    check_disposable(url)


@pytest.mark.parametrize(
    ("url", "problem"),
    [
        # The local Compose database: loopback, but not a test database.
        ("postgresql+psycopg://consoletrack:pw@localhost:5432/consoletrack", "does not contain"),
        ("postgresql+psycopg://ct@db.internal:5432/ct_test", "not loopback"),
        ("postgresql+psycopg://ct@consoletrack-pg-rw.consoletrack/app", "not loopback"),
        ("postgresql+psycopg://ct@localhost", "does not contain"),
    ],
)
def test_refuses_anything_else(url: str, problem: str) -> None:
    with pytest.raises(UnsafeTestDatabase, match=problem):
        check_disposable(url)


def test_error_hides_the_password() -> None:
    with pytest.raises(UnsafeTestDatabase) as exc:
        check_disposable("postgresql+psycopg://ct:hunter2@localhost/prod")
    assert "hunter2" not in str(exc.value)
