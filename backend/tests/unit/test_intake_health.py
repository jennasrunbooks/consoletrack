from __future__ import annotations

from datetime import timedelta

import pytest

from app.db.repositories import QueueReport
from app.enums import IntakeState
from app.services.intake_health import (
    DbIntakeStatus,
    IntakeSnapshot,
    derive_state,
    record_canary,
    record_report,
)
from tests.unit.conftest import NOW
from tests.unit.fakes import FakeStore

HEALTHY = IntakeSnapshot(
    last_canary_at=NOW - timedelta(minutes=10),
    queue_backlog=0,
    oldest_message_age_seconds=0,
    dead_letter_count=0,
    trail_logging=True,
    reported_at=NOW - timedelta(seconds=30),
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
    assert any("hasn't reported yet" in reason for reason in reasons)


def test_replay_wins_over_everything() -> None:
    result, reasons = derive_state(IntakeSnapshot(), now=NOW, replay=True)
    assert result is IntakeState.replay
    assert reasons


@pytest.mark.parametrize(
    ("changes", "expected", "reason"),
    [
        # The issue's boundaries: 9 vs 11 min backlog, 34/40/46 min canary.
        ({"oldest_message_age_seconds": 9 * 60}, IntakeState.live, None),
        ({"oldest_message_age_seconds": 11 * 60}, IntakeState.delayed, "11 min old"),
        ({"last_canary_at": NOW - timedelta(minutes=34)}, IntakeState.live, None),
        ({"last_canary_at": NOW - timedelta(minutes=40)}, IntakeState.delayed, "40 min old"),
        ({"last_canary_at": NOW - timedelta(minutes=46)}, IntakeState.down, "no canary for 46"),
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
        ({"oldest_message_age_seconds": None}, IntakeState.down, "SQS unreachable"),
        ({"reported_at": NOW - timedelta(minutes=3)}, IntakeState.live, None),
        (
            {"reported_at": NOW - timedelta(minutes=3, seconds=1)},
            IntakeState.down,
            "hasn't reported for 3 min",
        ),
        ({"reported_at": NOW - timedelta(minutes=20)}, IntakeState.down, "for 20 min"),
        ({"reported_at": None}, IntakeState.down, "hasn't reported yet"),
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


def test_db_status_reads_the_workers_row() -> None:
    store = FakeStore()
    with store.uow() as uow:
        assert DbIntakeStatus().snapshot(uow) == IntakeSnapshot()
        report = QueueReport(
            queue_backlog=2,
            oldest_message_age_seconds=None,
            dead_letter_count=0,
            trail_logging=True,
        )
        record_report(uow, report, now=NOW)
        record_canary(uow, NOW - timedelta(minutes=5))
        # An older canary (a redelivery) doesn't move it back.
        record_canary(uow, NOW - timedelta(minutes=20))
        snapshot = DbIntakeStatus().snapshot(uow)
    assert snapshot == IntakeSnapshot(
        last_canary_at=NOW - timedelta(minutes=5),
        queue_backlog=2,
        oldest_message_age_seconds=None,
        dead_letter_count=0,
        trail_logging=True,
        reported_at=NOW,
    )
    # A failed call stored as null says why, instead of a stale value.
    state_, reasons = derive_state(snapshot, now=NOW, replay=False)
    assert state_ is IntakeState.down
    assert any("SQS unreachable" in reason for reason in reasons)


def test_a_new_report_overwrites_old_values_with_nulls() -> None:
    store = FakeStore()
    with store.uow() as uow:
        record_report(uow, QueueReport(5, 30, 0, True), now=NOW - timedelta(minutes=1))
        record_report(uow, QueueReport(None, None, None, None), now=NOW)
        snapshot = DbIntakeStatus().snapshot(uow)
    assert snapshot.queue_backlog is None
    assert snapshot.trail_logging is None
    assert snapshot.reported_at == NOW
