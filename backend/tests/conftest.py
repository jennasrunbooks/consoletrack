"""Shared by unit, contract, and integration tests.

The app refuses to start without a SESSION_SECRET, so every `Settings()` built in tests gets
this throwaway value. It is not a real secret and signs nothing outside tests.
"""

from __future__ import annotations

import os

TEST_SESSION_SECRET = "test-only-session-secret-not-used-anywhere-else"
os.environ["SESSION_SECRET"] = TEST_SESSION_SECRET
