"""The change state machine (product-spec.md → Workflow and data model → Change flow).

`transition()` is the pure rule; `apply()` is the only code that assigns `ChangeRow.status`, and
it records the reviewer's decision whenever the change leaves review.

    detected ──analysis_written──▶ awaiting_review ──revert_requested──▶ reverting ──▶ reverted
                                                   ──accepted──────────▶ accepted      └▶ failed
                                                   ──codify_requested──▶ codifying ──▶ codified
                                                                                    └▶ failed

`openapi.yaml` also allows Accept straight from `detected` (before the agent has written its
summary), and a `failed` change can be reviewed again: Revert, Codify, or Accept.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from app.db.models import ChangeRow
from app.enums import ChangeDecision, ChangeStatus
from app.services.errors import InvalidTransition, MissingReason

INITIAL = ChangeStatus.detected


class ChangeEvent(StrEnum):
    analysis_written = "analysis_written"
    revert_requested = "revert_requested"
    revert_succeeded = "revert_succeeded"
    revert_failed = "revert_failed"
    accepted = "accepted"
    codify_requested = "codify_requested"
    codify_succeeded = "codify_succeeded"
    codify_failed = "codify_failed"


S, E = ChangeStatus, ChangeEvent

TRANSITIONS: dict[tuple[ChangeStatus, ChangeEvent], ChangeStatus] = {
    (S.detected, E.analysis_written): S.awaiting_review,
    # Review decisions, from review or after a failed revert/codify run.
    (S.awaiting_review, E.revert_requested): S.reverting,
    (S.failed, E.revert_requested): S.reverting,
    (S.detected, E.accepted): S.accepted,
    (S.awaiting_review, E.accepted): S.accepted,
    (S.failed, E.accepted): S.accepted,
    (S.awaiting_review, E.codify_requested): S.codifying,
    (S.failed, E.codify_requested): S.codifying,
    # Outcomes of the GitHub run or pull request.
    (S.reverting, E.revert_succeeded): S.reverted,
    (S.reverting, E.revert_failed): S.failed,
    (S.codifying, E.codify_succeeded): S.codified,
    (S.codifying, E.codify_failed): S.failed,
}

# Events that are a reviewer's decision, and the decision they record.
DECISIONS: dict[ChangeEvent, ChangeDecision] = {
    E.revert_requested: ChangeDecision.revert,
    E.accepted: ChangeDecision.accept,
    E.codify_requested: ChangeDecision.codify,
}
# Decisions that need a note: Accept is the whole record of why the change is fine.
REASON_REQUIRED = frozenset({E.accepted})

del S, E


def transition(current: ChangeStatus, event: ChangeEvent) -> ChangeStatus:
    """The state after `event`, or `InvalidTransition` if it isn't allowed from `current`."""
    try:
        return TRANSITIONS[(current, event)]
    except KeyError:
        raise InvalidTransition(current, event) from None


def apply(
    change: ChangeRow,
    event: ChangeEvent,
    *,
    now: datetime,
    actor: str | None = None,
    reason: str | None = None,
) -> ChangeStatus:
    """Move `change` through `event`, recording decision, decided_by, decided_at, and reason."""
    new_status = transition(change.status, event)
    decision = DECISIONS.get(event)
    if decision is not None:
        if not actor:
            raise ValueError(f"{event.value} needs the deciding user")
        reason = reason.strip() if reason else None
        if event in REASON_REQUIRED and not reason:
            raise MissingReason(f"{event.value} needs a note")
        change.decision = decision
        change.decided_by = actor
        change.decided_at = now
        change.reason = reason
    change.status = new_status
    return new_status
