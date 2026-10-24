from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.models import AgentRunRow, EvidenceRow
from app.enums import AgentRunOutcome, SubjectType
from app.services import incident_state
from app.services.incident_state import IncidentEvent
from tests.unit.conftest import NOW, ChangeFactory, add_analysis, link
from tests.unit.fakes import FakeStore


def test_list_newest_first_with_filters(viewer: TestClient, make_incident) -> None:
    old = make_incident(opened_at=NOW - timedelta(days=1), severity="warning")
    new = make_incident(opened_at=NOW - timedelta(minutes=5), namespace="other")
    resolved = make_incident(opened_at=NOW - timedelta(hours=3))
    incident_state.apply(resolved, IncidentEvent.alert_resolved, now=NOW)

    def ids(**params) -> list[int]:
        response = viewer.get("/api/v1/incidents", params=params)
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()["items"]]

    assert ids() == [new.id, resolved.id, old.id]
    assert ids(severity="warning") == [old.id]
    assert ids(namespace="other") == [new.id]
    assert ids(status=["resolved"]) == [resolved.id]
    assert ids(status=["open", "resolved"], namespace="demo") == [resolved.id, old.id]
    page = viewer.get("/api/v1/incidents", params={"page": 2, "page_size": 1}).json()
    assert [item["id"] for item in page["items"]] == [resolved.id]
    assert (page["total"], page["page"], page["page_size"]) == (3, 2, 1)


def test_list_rejects_unknown_status(viewer: TestClient) -> None:
    assert viewer.get("/api/v1/incidents?status=gone").status_code == 422


def test_detail(
    viewer: TestClient, make_incident, make_change: ChangeFactory, store: FakeStore
) -> None:
    incident = make_incident(
        labels={"alertname": "DemoAppAccessDenied", "namespace": "demo"},
        annotations={"summary": "AccessDenied from S3"},
    )
    incident_state.apply(incident, IncidentEvent.alert_resolved, now=NOW)
    change = make_change(title="s3:GetObject removed from the demo app role")
    link(store, incident, change)
    add_analysis(
        store,
        SubjectType.incident,
        incident.id,
        root_cause="policy edited",
        flags=["possible_prompt_injection"],
    )

    response = viewer.get(f"/api/v1/incidents/{incident.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "resolved"
    assert body["fingerprint"] == incident.fingerprint
    assert body["labels"] == {"alertname": "DemoAppAccessDenied", "namespace": "demo"}
    assert body["annotations"] == {"summary": "AccessDenied from S3"}
    assert body["time_to_resolve_seconds"] == 3600  # opened an hour before NOW
    assert body["analysis"]["root_cause"] == "policy edited"
    assert body["analysis"]["flags"] == ["possible_prompt_injection"]
    assert body["remediation"] is None
    assert body["linked_changes"] == [
        {
            "change": {
                "id": change.id,
                "title": "s3:GetObject removed from the demo app role",
                "event_name": "AuthorizeSecurityGroupIngress",
                "risk": None,
                "status": "detected",
            },
            "confidence": "high",
        }
    ]
    summary = viewer.get("/api/v1/incidents").json()["items"][0]
    assert summary["linked_changes"][0]["id"] == change.id


def test_open_incident_has_no_time_to_resolve(viewer: TestClient, make_incident) -> None:
    body = viewer.get(f"/api/v1/incidents/{make_incident().id}").json()
    assert body["resolved_at"] is None
    assert body["time_to_resolve_seconds"] is None


def test_detail_404(viewer: TestClient) -> None:
    response = viewer.get("/api/v1/incidents/7")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


# --- Evidence --------------------------------------------------------------------------------


def _run(store: FakeStore, subject_type: SubjectType, subject_id: int, minutes_ago: int) -> int:
    return store.agent_runs.insert(
        AgentRunRow(
            subject_type=subject_type,
            subject_id=subject_id,
            model="claude-haiku-5-5",
            started_at=NOW - timedelta(minutes=minutes_ago),
            outcome=AgentRunOutcome.succeeded,
        )
    ).id


def _evidence(store: FakeStore, run_id: int, seq: int, tool: str) -> None:
    store.evidence.insert(
        EvidenceRow(
            agent_run_id=run_id,
            seq=seq,
            tool_name=tool,
            args_json={"namespace": "demo"},
            result_excerpt="<script>alert(1)</script>",
        )
    )


def test_evidence_oldest_run_first_then_seq(
    viewer: TestClient, make_incident, store: FakeStore
) -> None:
    incident = make_incident()
    other = make_incident()
    later = _run(store, SubjectType.incident, incident.id, minutes_ago=1)
    earlier = _run(store, SubjectType.incident, incident.id, minutes_ago=10)
    _evidence(store, later, 1, "get_events")
    _evidence(store, earlier, 2, "get_pod_logs")
    _evidence(store, earlier, 1, "get_pods")
    _evidence(store, _run(store, SubjectType.incident, other.id, 5), 1, "not_mine")

    response = viewer.get(f"/api/v1/incidents/{incident.id}/evidence")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["tool_name"] for item in items] == ["get_pods", "get_pod_logs", "get_events"]
    assert items[0]["args"] == {"namespace": "demo"}
    # Untrusted text is returned verbatim; rendering it safely is the client's job.
    assert items[0]["result_excerpt"] == "<script>alert(1)</script>"


def test_evidence_for_a_change_and_tool_call_count(
    viewer: TestClient, make_change: ChangeFactory, store: FakeStore
) -> None:
    change = make_change()
    analysis = add_analysis(store, SubjectType.change, change.id)
    _evidence(store, analysis.agent_run_id, 1, "get_security_group")
    _evidence(store, analysis.agent_run_id, 2, "lookup_terraform_address")
    items = viewer.get(f"/api/v1/changes/{change.id}/evidence").json()["items"]
    assert [item["seq"] for item in items] == [1, 2]
    detail = viewer.get(f"/api/v1/changes/{change.id}").json()
    assert detail["analysis"]["agent_run"]["tool_calls"] == 2


@pytest.mark.parametrize("subject", ["changes", "incidents"])
def test_evidence_404_for_unknown_subject(viewer: TestClient, subject: str) -> None:
    response = viewer.get(f"/api/v1/{subject}/5/evidence")
    assert response.status_code == 404


def test_evidence_rejects_unknown_subject_path(viewer: TestClient) -> None:
    assert viewer.get("/api/v1/widgets/1/evidence").status_code == 422


# --- Postmortem ------------------------------------------------------------------------------


def test_postmortem_replaces_the_draft_on_the_latest_analysis(
    admin: TestClient, viewer: TestClient, make_incident, store: FakeStore
) -> None:
    incident = make_incident()
    add_analysis(store, SubjectType.incident, incident.id, postmortem_md="# Old run")
    latest = add_analysis(store, SubjectType.incident, incident.id, postmortem_md="# Draft")
    body = {"postmortem_md": "# Postmortem\n\n<script>alert(1)</script> stays text"}

    response = admin.put(f"/api/v1/incidents/{incident.id}/postmortem", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == latest.id
    assert response.json()["postmortem_md"] == body["postmortem_md"]
    assert latest.postmortem_md == body["postmortem_md"]
    assert store.commits == 1
    detail = viewer.get(f"/api/v1/incidents/{incident.id}").json()
    assert detail["analysis"]["postmortem_md"] == body["postmortem_md"]


def test_postmortem_can_be_cleared(admin: TestClient, make_incident, store: FakeStore) -> None:
    incident = make_incident()
    row = add_analysis(store, SubjectType.incident, incident.id, postmortem_md="# Draft")
    url = f"/api/v1/incidents/{incident.id}/postmortem"
    assert admin.put(url, json={"postmortem_md": ""}).status_code == 200
    assert row.postmortem_md == ""


def test_postmortem_needs_an_analysis(admin: TestClient, make_incident) -> None:
    url = f"/api/v1/incidents/{make_incident().id}/postmortem"
    response = admin.put(url, json={"postmortem_md": "# Postmortem"})
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "no_analysis"


def test_postmortem_errors(admin: TestClient, viewer: TestClient, make_incident) -> None:
    url = f"/api/v1/incidents/{make_incident().id}/postmortem"
    assert viewer.put(url, json={"postmortem_md": "x"}).status_code == 403
    assert admin.put(url, json={}).status_code == 422
    assert admin.put(url, json={"postmortem_md": "x" * 100_001}).status_code == 422
    missing = admin.put("/api/v1/incidents/999/postmortem", json={"postmortem_md": "x"})
    assert missing.status_code == 404


def test_reject_still_validates_its_body(admin: TestClient, make_incident) -> None:
    url = f"/api/v1/incidents/{make_incident().id}/remediation/reject"
    assert admin.post(url, json={"reason": " "}).status_code == 422
