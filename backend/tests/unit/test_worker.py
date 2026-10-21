"""The worker process: the triage poll loop and its heartbeat file."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app import worker
from app.settings import Settings

pytestmark = pytest.mark.anyio


class ScriptedTriage:
    """run_next answers from a list, then cancels the loop."""

    def __init__(self, answers: list[bool | Exception]) -> None:
        self.answers = answers
        self.calls = 0

    async def run_next(self) -> bool:
        self.calls += 1
        if not self.answers:
            raise asyncio.CancelledError
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


async def test_loop_survives_errors_and_touches_the_heartbeat(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat"
    triage = ScriptedTriage([True, RuntimeError("database down"), False])
    with pytest.raises(asyncio.CancelledError):
        await worker.run_triage_loop(
            triage,  # type: ignore[arg-type]
            poll_seconds=0,
            heartbeat_file=str(heartbeat),
        )
    assert triage.calls == 4
    assert heartbeat.exists()


def test_build_agents_needs_a_database() -> None:
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        worker.build_agents(Settings(database_url=None))


def test_build_agents_wires_the_manual_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    agents = worker.build_agents(Settings(database_url="postgresql+psycopg://u:p@127.0.0.1:1/db"))
    assert type(agents.triage._gate).__name__ == "ConfiguredPolicyGate"
    assert agents.changes is not None
