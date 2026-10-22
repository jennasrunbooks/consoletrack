"""Shared by unit, contract, and integration tests.

The app refuses to start without a SESSION_SECRET, so every `Settings()` built in tests gets
this throwaway value. It is not a real secret and signs nothing outside tests.

Telemetry goes to in-memory exporters (tests/telemetry_recorder.py), installed on import, before
any app is built; the `recorded` fixture reads them.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from tests.telemetry_recorder import RECORDED, Recorded

TEST_SESSION_SECRET = "test-only-session-secret-not-used-anywhere-else"
os.environ["SESSION_SECRET"] = TEST_SESSION_SECRET


@pytest.fixture
def anyio_backend() -> str:
    """Async tests (`pytest.mark.anyio`) run on asyncio only, like the worker."""
    return "asyncio"


@pytest.fixture
def recorded() -> Iterator[Recorded]:
    """The spans, logs, and metrics this test produces, and only those."""
    RECORDED.clear()
    yield RECORDED
    RECORDED.clear()
