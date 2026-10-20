"""POST /incidents/{id}/remediation/approve and /reject, and the remediation state machine."""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.enums import IncidentDecision, IncidentStatus, RemediationMode, RemediationStatus
from app.services import remediation_state
from app.services.errors import InvalidTransition
from app.services.remediation_state import RemediationEvent
from tests.unit.conftest import NOW
from tests.unit.fakes import FakeStore
from tests.unit.test_policy_gate import triaged


def url(incident_id: int, action: str) -> str:
    return f"/api/v1/incidents/{incident_id}/remediation/{action}"


def test_approve_starts_the_remediation(
    admin: TestClient, store: FakeStore, make_incident: Any
) -> None:
    incident, row = triaged(store, make_incident)
    response = admin.post(url(incident.id, "approve"), json={"note": "looks right"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "remediating"
    remediation = body["remediation"]
    assert remediation["status"] == "approved" and remediation["decided_by"] == "admin"
    assert remediation["mode"] == "manual"
    assert remediation["steps"][-1]["detail"] == "approved by admin: looks right"
    assert row.status is RemediationStatus.approved and row.decided_at == NOW
    assert incident.status is IncidentStatus.remediating


def test_approve_without_a_body(admin: TestClient, store: FakeStore, make_incident: Any) -> None:
    incident, _ = triaged(store, make_incident)
    response = admin.post(url(incident.id, "approve"))
    assert response.status_code == 200, response.text
    assert response.json()["remediation"]["steps"][-1]["detail"] == "approved by admin"


def test_approve_twice_is_a_conflict(
    admin: TestClient, store: FakeStore, make_incident: Any
) -> None:
    incident, _ = triaged(store, make_incident)
    assert admin.post(url(incident.id, "approve")).status_code == 200
    response = admin.post(url(incident.id, "approve"))
    assert response.status_code == 409
    assert response.json()["code"] == "no_proposed_remediation"


def test_approve_without_a_proposal_is_a_conflict(admin: TestClient, make_incident: Any) -> None:
    response = admin.post(url(make_incident().id, "approve"))
    assert response.status_code == 409


def test_approve_unknown_incident(admin: TestClient) -> None:
    assert admin.post(url(999, "approve")).status_code == 404


def test_approve_a_kind_the_backend_cant_apply_is_501(
    admin: TestClient, store: FakeStore, make_incident: Any
) -> None:
    proposal = {"kind": "rollback", "namespace": "demo", "deployment": "demo-app",
                "to_revision": None, "reason": "bad image"}  # fmt: skip
    incident, row = triaged(store, make_incident, proposal=proposal)
    response = admin.post(url(incident.id, "approve"))
    assert response.status_code == 501
    assert row.status is RemediationStatus.proposed
    assert incident.status is IncidentStatus.awaiting_review


def test_reject_closes_the_incident(
    admin: TestClient, store: FakeStore, make_incident: Any
) -> None:
    incident, row = triaged(store, make_incident)
    response = admin.post(url(incident.id, "reject"), json={"reason": "wrong container"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "closed"
    assert row.status is RemediationStatus.rejected and row.decided_by == "admin"
    assert row.mode is RemediationMode.manual
    assert incident.decision is IncidentDecision.rejected
    assert incident.reason == "wrong container"


def test_reject_needs_a_reason(admin: TestClient, store: FakeStore, make_incident: Any) -> None:
    incident, row = triaged(store, make_incident)
    assert admin.post(url(incident.id, "reject"), json={"reason": " "}).status_code == 422
    assert admin.post(url(incident.id, "reject"), json={}).status_code == 422
    assert row.status is RemediationStatus.proposed


@pytest.mark.parametrize(("action", "body"), [("approve", None), ("reject", {"reason": "no"})])
def test_viewers_cannot_decide(
    viewer: TestClient, store: FakeStore, make_incident: Any, action: str, body: Any
) -> None:
    incident, row = triaged(store, make_incident)
    assert viewer.post(url(incident.id, action), json=body).status_code == 403
    assert row.status is RemediationStatus.proposed


# --- The state machine ---------------------------------------------------------------------

S, E = RemediationStatus, RemediationEvent
ALLOWED = {
    (S.proposed, E.approved): S.approved,
    (S.proposed, E.rejected): S.rejected,
    (S.approved, E.claimed): S.running,
    (S.running, E.claimed): S.running,
    (S.running, E.downgraded): S.proposed,
    (S.running, E.succeeded): S.succeeded,
    (S.running, E.failed): S.failed,
}


def test_remediation_table_is_exactly_this() -> None:
    assert remediation_state.TRANSITIONS == ALLOWED


@pytest.mark.parametrize(("current", "event"), sorted(set(itertools.product(S, E)) - set(ALLOWED)))
def test_remediation_disallowed(current: RemediationStatus, event: RemediationEvent) -> None:
    with pytest.raises(InvalidTransition):
        remediation_state.transition(current, event)


def test_approval_needs_an_actor(store: FakeStore, make_incident: Any) -> None:
    _, row = triaged(store, make_incident)
    with pytest.raises(ValueError, match="needs the deciding"):
        remediation_state.apply(row, RemediationEvent.approved, now=NOW)
