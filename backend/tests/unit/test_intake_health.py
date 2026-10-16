from __future__ import annotations

from datetime import timedelta

import pytest

from app.enums import IntakeState
from app.services.intake_health import IntakeSnapshot, derive_state
from tests.unit.conftest import NOW

HEALTHY = IntakeSnapshot(
    last_canary_at=NOW - timedelta(minutes=10),
    queue_backlog=0,
    oldest_message_age_seconds=0,
    dead_letter_count=0,
    trail_logging=True,
)


def state(**changes: object) -> tuple[IntakeState, list[str]]:
    snapshot = IntakeSnapshot(**{**HEALTHY.__dict__, **changes})
    return derive_state(snapshot, now=NOW, replay=False)


def test_live_when_everything_reports_in() -> None:
    assert state() == (IntakeState.live, [])


def test_nothing_reported_yet_is_down_with_reasons() -> None:
    result, reasons = derive_state(IntakeSnapshot(), now=NOW, replay=False)
    assert result is IntakeState.down
    assert any("change queue" in reason for reason in reasons)
    assert any("canary" in reason for reason in reasons)
    assert any("trail" in reason for reason in reasons)


def test_replay_wins_over_everything() -> None:
    result, reasons = derive_state(IntakeSnapshot(), now=NOW, replay=True)
    assert result is IntakeState.replay
    assert reasons


@pytest.mark.parametrize(
    ("changes", "expected", "reason"),
    [
        ({"oldest_message_age_seconds": 600}, IntakeState.live, None),
        ({"oldest_message_age_seconds": 601}, IntakeState.delayed, "oldest queued message"),
        ({"last_canary_at": NOW - timedelta(minutes=35)}, IntakeState.live, None),
        ({"last_canary_at": NOW - timedelta(minutes=36)}, IntakeState.delayed, "36 min old"),
        ({"last_canary_at": NOW - timedelta(minutes=45)}, IntakeState.delayed, "canary"),
        ({"last_canary_at": NOW - timedelta(minutes=52)}, IntakeState.down, "no canary for 52"),
        ({"last_canary_at": None}, IntakeState.down, "no canary received"),
        ({"dead_letter_count": 3}, IntakeState.down, "3 message(s) in the dead-letter"),
        ({"dead_letter_count": None}, IntakeState.down, "dead-letter"),
        ({"trail_logging": False}, IntakeState.down, "isn't logging"),
        ({"trail_logging": None}, IntakeState.down, "trail status unknown"),
        ({"queue_backlog": None}, IntakeState.down, "SQS unreachable"),
    ],
)
def test_thresholds(changes: dict, expected: IntakeState, reason: str | None) -> None:
    result, reasons = state(**changes)
    assert result is expected
    if reason is None:
        assert reasons == []
    else:
        assert any(reason in line for line in reasons), reasons


def test_down_outranks_delayed_regardless_of_order() -> None:
    result, reasons = state(oldest_message_age_seconds=3600, dead_letter_count=1)
    assert result is IntakeState.down
    assert len(reasons) == 2
    result, _ = state(dead_letter_count=1, last_canary_at=NOW - timedelta(minutes=40))
    assert result is IntakeState.down
