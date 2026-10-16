"""Refuse to run integration tests against a database that might hold real data.

The tests truncate every table and run `alembic downgrade base`, which drops them. So a
`CT_TEST_DATABASE_URL` must point at a loopback host AND a database whose name contains "test".
Both are required: the local Compose database (`consoletrack` on localhost) passes the host
check alone, and a remote database named `*_test` passes the name check alone.
"""

from __future__ import annotations

from sqlalchemy.engine import make_url

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class UnsafeTestDatabase(Exception):
    pass


def check_disposable(url: str) -> None:
    parsed = make_url(url)
    problems = []
    if (parsed.host or "").lower() not in LOOPBACK_HOSTS:
        allowed = ", ".join(sorted(LOOPBACK_HOSTS))
        problems.append(f"host {parsed.host!r} is not loopback ({allowed})")
    if "test" not in (parsed.database or "").lower():
        problems.append(f"database name {parsed.database!r} does not contain 'test'")
    if problems:
        raise UnsafeTestDatabase(
            "refusing to run integration tests: they drop and truncate every table, and "
            f"CT_TEST_DATABASE_URL ({parsed.render_as_string(hide_password=True)}) fails the "
            "safety check: " + "; ".join(problems)
        )
