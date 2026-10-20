"""The triage queue (claim, lease, re-run) and how a run's result is recorded."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.enums import AgentRunOutcome, IncidentStatus, SubjectType
from app.services import incident_state, triage
from app.services.incident_state import IncidentEvent
from tests.unit.agent_fakes import submit
from tests.unit.conftest import NOW, add_analysis
from tests.unit.fakes import FakeStore

LEASE = timedelta(minutes=5)


def awaiting_review(make_incident: Any, **fields: Any) -> Any:
    row = make_incident(**fields)
    incident_state.apply(row, IncidentEvent.triage_started, now=NOW)
    incident_state.apply(row, IncidentEvent.analysis_written, now=NOW)
    return row


def test_claim_moves_open_to_triaging_and_takes_a_lease(
    store: FakeStore, make_incident: Any
) -> None:
    later = make_incident(triage_requested_at=NOW - timedelta(minutes=1))
    first = make_incident(triage_requested_at=NOW - timedelta(minutes=2), labels={"a": "b"})
    claim = triage.claim_next(store.uow(), now=NOW, lease=LEASE)
    assert claim is not None and claim.incident_id == first.id
    assert claim.labels == {"a": "b"} and claim.model is None
    assert first.status is IncidentStatus.triaging
    assert first.triage_requested_at == NOW + LEASE
    # Leased: the next claim takes the other one, then nothing until the lease expires.
    assert triage.claim_next(store.uow(), now=NOW, lease=LEASE).incident_id == later.id
    assert triage.claim_next(store.uow(), now=NOW, lease=LEASE) is None
    # Both leases expire together; a crashed run's incident is picked up again, still triaging.
    again = [triage.claim_next(store.uow(), now=NOW + LEASE, lease=LEASE) for _ in range(3)]
    assert [claim.incident_id if claim else None for claim in again] == [later.id, first.id, None]
    assert first.status is IncidentStatus.triaging


def test_claim_drops_a_request_left_on_an_incident_that_moved_on(
    store: FakeStore, make_incident: Any
) -> None:
    row = awaiting_review(make_incident)
    incident_state.apply(row, IncidentEvent.remediation_approved, now=NOW)
    row.triage_requested_at = NOW
    assert triage.claim_next(store.uow(), now=NOW, lease=LEASE) is None
    assert row.triage_requested_at is None
    assert row.status is IncidentStatus.remediating


def test_resolved_incidents_are_never_claimed(store: FakeStore, make_incident: Any) -> None:
    row = make_incident(triage_requested_at=NOW)
    incident_state.apply(row, IncidentEvent.alert_resolved, now=NOW)
    assert triage.claim_next(store.uow(), now=NOW, lease=LEASE) is None


def test_record_triage_without_an_incident_is_not_found(store: FakeStore) -> None:
    from app.services.errors import NotFound

    with pytest.raises(NotFound):
        triage.record_triage(store.uow(), 99, run_id=1, analysis=None, stopped="x", now=NOW)


def test_rerun_links_each_change_once(
    store: FakeStore, make_incident: Any, make_change: Any
) -> None:
    from app.agents.schema import SubmitAnalysis

    change = make_change()
    row = make_incident()
    incident_state.apply(row, IncidentEvent.triage_started, now=NOW)
    analysis = SubmitAnalysis.model_validate(
        {**submit().tool_calls[0].input, "linked_change_ids": [change.id]}
    )
    triage.record_triage(store.uow(), row.id, run_id=1, analysis=analysis, stopped="", now=NOW)
    incident_state.apply(row, IncidentEvent.triage_started, now=NOW)
    triage.record_triage(store.uow(), row.id, run_id=2, analysis=analysis, stopped="", now=NOW)
    assert len(store.links.rows) == 1
    assert len(store.analyses.rows) == 2


# --- POST /incidents/{id}/triage --------------------------------------------------------------


def test_rerun_queues_the_incident_with_the_escalation_model(
    admin: TestClient, store: FakeStore, make_incident: Any
) -> None:
    row = awaiting_review(make_incident)
    add_analysis(store, SubjectType.incident, row.id)
    response = admin.post(f"/api/v1/incidents/{row.id}/triage", json={"model": "claude-sonnet-5-5"})
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "triaging"
    assert row.status is IncidentStatus.triaging
    assert (row.triage_requested_at, row.triage_model) == (NOW, "claude-sonnet-5-5")
    claim = triage.claim_next(store.uow(), now=NOW, lease=LEASE)
    assert claim is not None and claim.model == "claude-sonnet-5-5"


@pytest.mark.parametrize("body", [None, {}, {"model": None}, {"model": "claude-haiku-5-5"}])
def test_rerun_with_the_default_model_stores_no_override(
    admin: TestClient, make_incident: Any, body: Any
) -> None:
    row = awaiting_review(make_incident)
    response = admin.post(f"/api/v1/incidents/{row.id}/triage", json=body)
    assert response.status_code == 202, response.text
    assert row.triage_model is None and row.triage_requested_at == NOW


def test_rerun_from_open_is_allowed(admin: TestClient, make_incident: Any) -> None:
    row = make_incident()
    assert admin.post(f"/api/v1/incidents/{row.id}/triage").status_code == 202
    assert row.status is IncidentStatus.triaging


def test_rerun_rejects_other_models(admin: TestClient, make_incident: Any) -> None:
    row = awaiting_review(make_incident)
    response = admin.post(f"/api/v1/incidents/{row.id}/triage", json={"model": "gpt-5"})
    assert response.status_code == 422
    assert "claude-haiku-5-5" in response.json()["message"]
    assert row.status is IncidentStatus.awaiting_review


def test_rerun_conflicts_while_triaging_or_after_resolution(
    admin: TestClient, make_incident: Any
) -> None:
    row = make_incident()
    incident_state.apply(row, IncidentEvent.triage_started, now=NOW)
    assert admin.post(f"/api/v1/incidents/{row.id}/triage").status_code == 409
    incident_state.apply(row, IncidentEvent.alert_resolved, now=NOW)
    assert admin.post(f"/api/v1/incidents/{row.id}/triage").status_code == 409


def test_rerun_auth_and_not_found(
    admin: TestClient, viewer: TestClient, anon: TestClient, make_incident: Any
) -> None:
    url = f"/api/v1/incidents/{make_incident().id}/triage"
    assert viewer.post(url).status_code == 403
    assert anon.post(url).status_code == 401
    assert admin.post("/api/v1/incidents/999/triage").status_code == 404


def test_outcome_values_include_limit_reached() -> None:
    assert AgentRunOutcome("limit_reached") is AgentRunOutcome.limit_reached
