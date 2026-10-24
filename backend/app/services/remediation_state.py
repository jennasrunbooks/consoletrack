"""The remediation state machine (openapi.yaml → RemediationStatus).

`transition()` is the pure rule; `apply()` is the only code that assigns `RemediationRow.status`.

    proposed ──approved──▶ approved    (an admin, or the policy gate in auto mode)
    proposed ──rejected──▶ rejected    (an admin)
    approved ──claimed───▶ running     (the backend's apply loop takes it)
    running  ──claimed───▶ running     (a crashed backend's lease expired; another one recovers it)
    running  ──downgraded▶ proposed    (auto only: the validator refused it before anything was
                                        applied, so it waits for a human like any manual proposal)
    running  ──succeeded─▶ succeeded
    running  ──failed────▶ failed      (refused, dry run or patch failed, or rolled back)
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from app.db.models import RemediationRow
from app.enums import RemediationMode, RemediationStatus
from app.services.errors import InvalidTransition


class RemediationEvent(StrEnum):
    approved = "approved"
    rejected = "rejected"
    claimed = "claimed"
    downgraded = "downgraded"
    succeeded = "succeeded"
    failed = "failed"


S, E = RemediationStatus, RemediationEvent

TRANSITIONS: dict[tuple[RemediationStatus, RemediationEvent], RemediationStatus] = {
    (S.proposed, E.approved): S.approved,
    (S.proposed, E.rejected): S.rejected,
    (S.approved, E.claimed): S.running,
    (S.running, E.claimed): S.running,
    (S.running, E.downgraded): S.proposed,
    (S.running, E.succeeded): S.succeeded,
    (S.running, E.failed): S.failed,
}

del S, E


def transition(current: RemediationStatus, event: RemediationEvent) -> RemediationStatus:
    try:
        return TRANSITIONS[(current, event)]
    except KeyError:
        raise InvalidTransition(current, event) from None


def apply(
    row: RemediationRow,
    event: RemediationEvent,
    *,
    now: datetime,
    actor: str | None = None,
    reason: str | None = None,
) -> RemediationStatus:
    """Move `row` through `event`. Approving and rejecting record who decided and when; a
    downgrade hands the proposal back to a human with the reason."""
    new_status = transition(row.status, event)
    if event in (RemediationEvent.approved, RemediationEvent.rejected):
        if not actor:
            raise ValueError(f"{event.value} needs the deciding user or policy")
        row.decided_by = actor
        row.decided_at = now
    if event is RemediationEvent.downgraded:
        row.mode = RemediationMode.manual
        row.gate_reason = reason
        row.decided_by = None
        row.decided_at = None
    if event is RemediationEvent.failed:
        row.error = reason
    if new_status is not RemediationStatus.running:
        row.lease_until = None
    row.status = new_status
    return new_status
