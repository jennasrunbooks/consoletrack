"""Intake health: is CloudTrail → EventBridge → SQS still delivering?

product-spec.md → Intake health and canary:

- Live: worker polling, oldest queued message under 10 min, canary within the last 35 min.
- Delayed: oldest message over 10 min, or canary 35 to 45 min old.
- Down: SQS unreachable, dead-letter messages, trail not logging, or no canary for 45 min.
- Replay: `INTAKE_MODE=replay` (recorded fixtures).

The intake worker (`app/intake/`) stores the queue, trail, and canary numbers in the
`intake_reports` row every `INTAKE_REPORT_SECONDS`; `DbIntakeStatus` reads them back for the API,
which runs in another pod. A report older than `REPORT_STALE` means the worker isn't running, so
the state is Down. `UnreportedIntakeStatus` (no database, replay) reports nothing: Down with
reasons that say so, rather than a reassuring Live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from app.db.repositories import QueueReport, UnitOfWork
from app.enums import IntakeState
from app.schemas import IntakeHealth

OLDEST_MESSAGE_DELAYED = timedelta(minutes=10)
CANARY_DELAYED = timedelta(minutes=35)
CANARY_DOWN = timedelta(minutes=45)
# Three missed 60-second reports.
REPORT_STALE = timedelta(minutes=3)


@dataclass(frozen=True)
class IntakeSnapshot:
    """The worker's last report. None means "not reported" (or SQS/CloudTrail unreachable)."""

    last_canary_at: datetime | None = None
    queue_backlog: int | None = None
    oldest_message_age_seconds: int | None = None
    dead_letter_count: int | None = None
    trail_logging: bool | None = None
    # When the worker last wrote the queue and trail fields.
    reported_at: datetime | None = None


class IntakeStatusSource(Protocol):
    def snapshot(self, uow: UnitOfWork) -> IntakeSnapshot: ...


class UnreportedIntakeStatus:
    """Used until the intake worker (#9) stores its reports."""

    def snapshot(self, uow: UnitOfWork) -> IntakeSnapshot:
        return IntakeSnapshot()


class DbIntakeStatus:
    """The worker's report from the `intake_reports` row (live mode)."""

    def snapshot(self, uow: UnitOfWork) -> IntakeSnapshot:
        row = uow.intake_reports.get()
        if row is None:
            return IntakeSnapshot()
        return IntakeSnapshot(
            last_canary_at=row.last_canary_at,
            queue_backlog=row.queue_backlog,
            oldest_message_age_seconds=row.oldest_message_age_seconds,
            dead_letter_count=row.dead_letter_count,
            trail_logging=row.trail_logging,
            reported_at=row.reported_at,
        )


def record_report(uow: UnitOfWork, report: QueueReport, *, now: datetime) -> None:
    """The worker's periodic report. Commits."""
    uow.intake_reports.save_report(report, reported_at=now)
    uow.commit()


def record_canary(uow: UnitOfWork, event_time: datetime) -> None:
    """A canary arrived through the intake. The caller commits (with the message's batch)."""
    uow.intake_reports.save_canary(event_time)


@dataclass
class _Verdict:
    state: IntakeState = IntakeState.live
    reasons: list[str] = field(default_factory=list)

    def flag(self, state: IntakeState, reason: str) -> None:
        if state is IntakeState.down or self.state is IntakeState.live:
            self.state = state
        self.reasons.append(reason)


def _minutes(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60)


def derive_state(
    snapshot: IntakeSnapshot, *, now: datetime, replay: bool
) -> tuple[IntakeState, list[str]]:
    if replay:
        return IntakeState.replay, ["replay mode: events are recorded fixtures, not live AWS"]
    verdict = _Verdict()
    down, delayed = IntakeState.down, IntakeState.delayed

    if snapshot.reported_at is None:
        verdict.flag(down, "the intake worker hasn't reported yet")
    elif now - snapshot.reported_at > REPORT_STALE:
        age = now - snapshot.reported_at
        verdict.flag(down, f"the intake worker hasn't reported for {_minutes(age)} min")

    if snapshot.queue_backlog is None or snapshot.oldest_message_age_seconds is None:
        verdict.flag(down, "the worker hasn't reported the change queue (SQS unreachable?)")
    elif snapshot.oldest_message_age_seconds > OLDEST_MESSAGE_DELAYED.total_seconds():
        age = timedelta(seconds=snapshot.oldest_message_age_seconds)
        verdict.flag(delayed, f"oldest queued message is {_minutes(age)} min old")

    if snapshot.dead_letter_count is None:
        verdict.flag(down, "dead-letter queue count unknown")
    elif snapshot.dead_letter_count > 0:
        verdict.flag(down, f"{snapshot.dead_letter_count} message(s) in the dead-letter queue")

    if snapshot.trail_logging is None:
        verdict.flag(down, "CloudTrail trail status unknown")
    elif not snapshot.trail_logging:
        verdict.flag(down, "the CloudTrail trail isn't logging")

    if snapshot.last_canary_at is None:
        verdict.flag(down, "no canary received yet")
    else:
        age = now - snapshot.last_canary_at
        if age > CANARY_DOWN:
            verdict.flag(down, f"no canary for {_minutes(age)} min")
        elif age > CANARY_DELAYED:
            verdict.flag(delayed, f"canary is {_minutes(age)} min old")

    return verdict.state, verdict.reasons


def get_intake_health(
    uow: UnitOfWork,
    source: IntakeStatusSource,
    *,
    now: datetime,
    region: str,
    replay: bool,
    grafana_url: str | None,
) -> IntakeHealth:
    snapshot = source.snapshot(uow)
    state, reasons = derive_state(snapshot, now=now, replay=replay)
    return IntakeHealth(
        state=state,
        reasons=reasons,
        region=region,
        checked_at=now,
        last_event_at=uow.changes.last_detected_at(),
        last_canary_at=snapshot.last_canary_at,
        queue_backlog=snapshot.queue_backlog,
        oldest_message_age_seconds=snapshot.oldest_message_age_seconds,
        dead_letter_count=snapshot.dead_letter_count,
        trail_logging=snapshot.trail_logging,
        grafana_url=grafana_url,
    )
