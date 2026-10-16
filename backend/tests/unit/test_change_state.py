from __future__ import annotations

import itertools

import pytest

from app.db.models import ChangeRow
from app.enums import ChangeDecision, ChangeStatus
from app.services import change_state
from app.services.change_state import ChangeEvent, transition
from app.services.errors import InvalidTransition, MissingReason
from tests.unit.conftest import NOW
from tests.unit.fakes import FakeStore

S, E = ChangeStatus, ChangeEvent

ALLOWED = {
    (S.detected, E.analysis_written): S.awaiting_review,
    (S.awaiting_review, E.revert_requested): S.reverting,
    (S.reverting, E.revert_succeeded): S.reverted,
    (S.reverting, E.revert_failed): S.failed,
    (S.detected, E.accepted): S.accepted,
    (S.awaiting_review, E.accepted): S.accepted,
    (S.awaiting_review, E.codify_requested): S.codifying,
    (S.codifying, E.codify_succeeded): S.codified,
    (S.codifying, E.codify_failed): S.failed,
    # A failed revert or codify can be reviewed again.
    (S.failed, E.revert_requested): S.reverting,
    (S.failed, E.codify_requested): S.codifying,
    (S.failed, E.accepted): S.accepted,
}
DISALLOWED = sorted(set(itertools.product(S, E)) - set(ALLOWED))


def test_table_is_exactly_the_spec() -> None:
    assert change_state.TRANSITIONS == ALLOWED


@pytest.mark.parametrize(("current", "event"), sorted(ALLOWED))
def test_allowed(current: ChangeStatus, event: ChangeEvent) -> None:
    assert transition(current, event) is ALLOWED[(current, event)]


@pytest.mark.parametrize(("current", "event"), DISALLOWED)
def test_disallowed(current: ChangeStatus, event: ChangeEvent) -> None:
    with pytest.raises(InvalidTransition) as exc:
        transition(current, event)
    assert exc.value.current is current
    assert exc.value.event is event


@pytest.mark.parametrize("terminal", [S.reverted, S.accepted, S.codified])
def test_closed_states_accept_no_events(terminal: ChangeStatus) -> None:
    for event in E:
        with pytest.raises(InvalidTransition):
            transition(terminal, event)


def _change(status: ChangeStatus = S.detected) -> ChangeRow:
    row = FakeStore().changes.insert(ChangeRow())  # column default: detected
    path = {
        S.detected: [],
        S.awaiting_review: [E.analysis_written],
        S.reverting: [E.analysis_written, E.revert_requested],
    }[status]
    for event in path:
        change_state.apply(row, event, now=NOW, actor="admin")
    return row


def test_accept_records_the_decision() -> None:
    row = _change(S.awaiting_review)
    assert change_state.apply(row, E.accepted, now=NOW, actor="admin", reason="  intended  ") is (
        S.accepted
    )
    assert row.status is S.accepted
    assert row.decision is ChangeDecision.accept
    assert row.decided_by == "admin"
    assert row.decided_at == NOW
    assert row.reason == "intended"


@pytest.mark.parametrize("note", [None, "", "   "])
def test_accept_needs_a_note(note: str | None) -> None:
    row = _change(S.awaiting_review)
    with pytest.raises(MissingReason):
        change_state.apply(row, E.accepted, now=NOW, actor="admin", reason=note)
    assert row.status is S.awaiting_review
    assert row.decision is None


def test_decisions_need_an_actor() -> None:
    row = _change(S.awaiting_review)
    with pytest.raises(ValueError, match="deciding user"):
        change_state.apply(row, E.revert_requested, now=NOW)
    assert row.status is S.awaiting_review


def test_revert_records_decision_and_keeps_it_through_the_outcome() -> None:
    row = _change(S.awaiting_review)
    change_state.apply(row, E.revert_requested, now=NOW, actor="admin")
    assert (row.decision, row.decided_by, row.reason) == (ChangeDecision.revert, "admin", None)
    change_state.apply(row, E.revert_succeeded, now=NOW)
    assert row.status is S.reverted
    assert row.decision is ChangeDecision.revert
    assert row.decided_by == "admin"


def test_invalid_transition_leaves_the_row_untouched() -> None:
    row = _change(S.reverting)
    with pytest.raises(InvalidTransition):
        change_state.apply(row, E.accepted, now=NOW, actor="admin", reason="too late")
    assert row.status is S.reverting
    assert row.decision is ChangeDecision.revert
