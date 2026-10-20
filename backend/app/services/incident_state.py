"""The incident state machine (product-spec.md → Incident flow).

`transition()` is the pure rule; `apply()` is the only code that assigns `IncidentRow.status`,
and it records decision, decided_by, decided_at, and reason on the two terminal states.

    open            ──triage_started───────▶ triaging
    triaging        ──analysis_written─────▶ awaiting_review
    triaging        ──triage_incomplete────▶ awaiting_review (the run stopped without one)
    awaiting_review ──triage_started───────▶ triaging        (re-run)
    awaiting_review ──remediation_approved─▶ remediating
    awaiting_review ──remediation_rejected─▶ closed          (reason required)
    any unresolved  ──alert_resolved───────▶ resolved
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from app.db.models import IncidentRow
from app.enums import ACTIVE_INCIDENT_STATUSES, IncidentDecision, IncidentStatus
from app.services.errors import InvalidTransition, MissingReason

INITIAL = IncidentStatus.open
# Who resolves an incident when Alertmanager says the alert stopped firing.
ALERTMANAGER = "alertmanager"


class IncidentEvent(StrEnum):
    triage_started = "triage_started"
    analysis_written = "analysis_written"
    # The agent run hit a limit or failed: the incident waits for a human without an analysis.
    triage_incomplete = "triage_incomplete"
    remediation_approved = "remediation_approved"
    remediation_rejected = "remediation_rejected"
    alert_resolved = "alert_resolved"


S, E = IncidentStatus, IncidentEvent

TRANSITIONS: dict[tuple[IncidentStatus, IncidentEvent], IncidentStatus] = {
    (S.open, E.triage_started): S.triaging,
    # Re-run triage, e.g. with the escalation model.
    (S.awaiting_review, E.triage_started): S.triaging,
    (S.triaging, E.analysis_written): S.awaiting_review,
    (S.triaging, E.triage_incomplete): S.awaiting_review,
    (S.awaiting_review, E.remediation_approved): S.remediating,
    (S.awaiting_review, E.remediation_rejected): S.closed,
    # A resolved notification ends the episode from any unresolved state.
    **{(status, E.alert_resolved): S.resolved for status in ACTIVE_INCIDENT_STATUSES},
}

# Terminal states and the decision each one records.
DECISIONS: dict[IncidentStatus, IncidentDecision] = {
    S.resolved: IncidentDecision.resolved,
    S.closed: IncidentDecision.rejected,
}
REASON_REQUIRED = frozenset({E.remediation_rejected})

del S, E


def transition(current: IncidentStatus, event: IncidentEvent) -> IncidentStatus:
    """The state after `event`, or `InvalidTransition` if it isn't allowed from `current`."""
    try:
        return TRANSITIONS[(current, event)]
    except KeyError:
        raise InvalidTransition(current, event) from None


def apply(
    incident: IncidentRow,
    event: IncidentEvent,
    *,
    now: datetime,
    actor: str | None = None,
    reason: str | None = None,
) -> IncidentStatus:
    """Move `incident` through `event`; terminal states record who decided and why."""
    new_status = transition(incident.status, event)
    decision = DECISIONS.get(new_status)
    if decision is not None:
        reason = reason.strip() if reason else None
        if event in REASON_REQUIRED and not reason:
            raise MissingReason(f"{event.value} needs a reason")
        if event is IncidentEvent.alert_resolved:
            actor = actor or ALERTMANAGER
            reason = reason or "Alertmanager sent a resolved notification"
            incident.resolved_at = now
        if not actor:
            raise ValueError(f"{event.value} needs the deciding user")
        incident.decision = decision
        incident.decided_by = actor
        incident.decided_at = now
        incident.reason = reason
    incident.status = new_status
    return new_status
