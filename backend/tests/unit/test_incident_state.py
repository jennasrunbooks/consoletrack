from __future__ import annotations

import itertools

import pytest

from app.db.models import IncidentRow
from app.enums import IncidentDecision, IncidentStatus
from app.services import incident_state
from app.services.errors import InvalidTransition, MissingReason
from app.services.incident_state import IncidentEvent, transition
from tests.unit.conftest import NOW
from tests.unit.fakes import FakeStore

S, E = IncidentStatus, IncidentEvent

ALLOWED = {
    (S.open, E.triage_started): S.triaging,
    (S.triaging, E.analysis_written): S.awaiting_review,
    (S.awaiting_review, E.triage_started): S.triaging,
    (S.awaiting_review, E.remediation_approved): S.remediating,
    (S.awaiting_review, E.remediation_rejected): S.closed,
    (S.open, E.alert_resolved): S.resolved,
    (S.triaging, E.alert_resolved): S.resolved,
    (S.awaiting_review, E.alert_resolved): S.resolved,
    (S.remediating, E.alert_resolved): S.resolved,
}
DISALLOWED = sorted(set(itertools.product(S, E)) - set(ALLOWED))


def test_table_is_exactly_the_spec() -> None:
    assert incident_state.TRANSITIONS == ALLOWED


@pytest.mark.parametrize(("current", "event"), sorted(ALLOWED))
def test_allowed(current: IncidentStatus, event: IncidentEvent) -> None:
    assert transition(current, event) is ALLOWED[(current, event)]


@pytest.mark.parametrize(("current", "event"), DISALLOWED)
def test_disallowed(current: IncidentStatus, event: IncidentEvent) -> None:
    with pytest.raises(InvalidTransition):
        transition(current, event)


def _incident(*events: IncidentEvent) -> IncidentRow:
    row = FakeStore().incidents.insert(IncidentRow(opened_at=NOW))  # column default: open
    for event in events:
        incident_state.apply(row, event, now=NOW)
    return row


def test_non_terminal_transitions_record_no_decision() -> None:
    row = _incident(E.triage_started, E.analysis_written, E.remediation_approved)
    assert row.status is S.remediating
    assert (row.decision, row.decided_by, row.reason, row.resolved_at) == (None,) * 4


@pytest.mark.parametrize(
    "events",
    [
        (),
        (E.triage_started,),
        (E.triage_started, E.analysis_written),
        (E.triage_started, E.analysis_written, E.remediation_approved),
    ],
)
def test_resolved_webhook_resolves_any_active_state(events: tuple[IncidentEvent, ...]) -> None:
    row = _incident(*events)
    incident_state.apply(row, E.alert_resolved, now=NOW)
    assert row.status is S.resolved
    assert row.resolved_at == NOW
    assert row.decision is IncidentDecision.resolved
    assert row.decided_by == incident_state.ALERTMANAGER
    assert row.reason


def test_reject_closes_with_reason_and_actor() -> None:
    row = _incident(E.triage_started, E.analysis_written)
    incident_state.apply(row, E.remediation_rejected, now=NOW, actor="admin", reason=" wrong pod ")
    assert row.status is S.closed
    assert (row.decision, row.decided_by, row.decided_at, row.reason) == (
        IncidentDecision.rejected,
        "admin",
        NOW,
        "wrong pod",
    )
    assert row.resolved_at is None


@pytest.mark.parametrize("reason", [None, "", "  "])
def test_reject_needs_a_reason(reason: str | None) -> None:
    row = _incident(E.triage_started, E.analysis_written)
    with pytest.raises(MissingReason):
        incident_state.apply(row, E.remediation_rejected, now=NOW, actor="admin", reason=reason)
    assert row.status is S.awaiting_review


def test_reject_needs_an_actor() -> None:
    row = _incident(E.triage_started, E.analysis_written)
    with pytest.raises(ValueError, match="deciding user"):
        incident_state.apply(row, E.remediation_rejected, now=NOW, reason="no")
    assert row.status is S.awaiting_review


@pytest.mark.parametrize("terminal", [S.resolved, S.closed])
def test_terminal_states_accept_no_events(terminal: IncidentStatus) -> None:
    for event in E:
        with pytest.raises(InvalidTransition):
            transition(terminal, event)
