from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.models import RemediationRow
from app.enums import ChangeStatus, RemediationKind, Risk, SubjectType, Via
from app.services.change_state import ChangeEvent
from tests.unit.conftest import NOW, ChangeFactory, add_analysis, link
from tests.unit.fakes import FakeStore

E = ChangeEvent


def ids(response) -> list[int]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


# --- GET /api/v1/changes ---------------------------------------------------------------------


def test_list_is_newest_first_and_shaped_like_the_contract(
    viewer: TestClient, make_change: ChangeFactory
) -> None:
    older = make_change(event_time=NOW - timedelta(days=2))
    newer = make_change(event_time=NOW - timedelta(hours=1), title=None)
    body = viewer.get("/api/v1/changes").json()
    assert [item["id"] for item in body["items"]] == [newer.id, older.id]
    assert body["total"] == 2
    assert (body["page"], body["page_size"]) == (1, 25)
    first = body["items"][0]
    assert first["title"] == "AuthorizeSecurityGroupIngress on demo-app-sg"
    assert first["status"] == "detected"
    assert first["risk"] is None
    assert first["linked_incidents"] == []


def test_filters_combine_with_and_and_repeat_with_or(
    viewer: TestClient, make_change: ChangeFactory
) -> None:
    sg = make_change(risk=Risk.high)
    iam = make_change(service="iam", event_name="DeleteRolePolicy", via=Via.cli, risk=Risk.medium)
    accepted = make_change(E.analysis_written, E.accepted, risk=Risk.low)

    assert ids(viewer.get("/api/v1/changes", params={"service": "iam"})) == [iam.id]
    assert set(ids(viewer.get("/api/v1/changes?risk=high&risk=medium"))) == {sg.id, iam.id}
    assert ids(viewer.get("/api/v1/changes?risk=high&via=cli")) == []
    assert ids(viewer.get("/api/v1/changes?status=accepted&status=reverted")) == [accepted.id]
    assert ids(viewer.get("/api/v1/changes?via=cli")) == [iam.id]


def test_principal_filter_matches_arn_or_session_case_insensitively(
    viewer: TestClient, make_change: ChangeFactory
) -> None:
    jdev = make_change()
    ops = make_change(principal_arn="arn:aws:iam::123456789012:user/Ops-Admin", session_name=None)
    assert ids(viewer.get("/api/v1/changes", params={"principal": "JDEV"})) == [jdev.id]
    assert ids(viewer.get("/api/v1/changes", params={"principal": "ops-admin"})) == [ops.id]


def test_free_text_search(viewer: TestClient, make_change: ChangeFactory) -> None:
    target = make_change(resource_name="demo-app-data", event_name="PutBucketPolicy")
    make_change()
    assert ids(viewer.get("/api/v1/changes", params={"q": "bucketpolicy"})) == [target.id]
    assert ids(viewer.get("/api/v1/changes", params={"q": "app-data"})) == [target.id]
    # LIKE wildcards are literal.
    assert ids(viewer.get("/api/v1/changes", params={"q": "%"})) == []


def test_date_range_is_inclusive_since_exclusive_until(
    viewer: TestClient, make_change: ChangeFactory
) -> None:
    at = NOW - timedelta(days=1)
    change = make_change(event_time=at)
    make_change(event_time=at - timedelta(days=3))
    params = {"since": at.isoformat(), "until": (at + timedelta(seconds=1)).isoformat()}
    assert ids(viewer.get("/api/v1/changes", params=params)) == [change.id]
    assert ids(viewer.get("/api/v1/changes", params={"until": at.isoformat()})) != [change.id]


def test_pagination(viewer: TestClient, make_change: ChangeFactory) -> None:
    rows = [make_change() for _ in range(5)]  # make_change spaces them an hour apart
    newest_first = [row.id for row in rows]
    page2 = viewer.get("/api/v1/changes", params={"page": 2, "page_size": 2}).json()
    assert [item["id"] for item in page2["items"]] == newest_first[2:4]
    assert (page2["total"], page2["page"], page2["page_size"]) == (5, 2, 2)
    beyond = viewer.get("/api/v1/changes", params={"page": 9, "page_size": 2}).json()
    assert beyond["items"] == [] and beyond["total"] == 5


@pytest.mark.parametrize(
    "query",
    ["page=0", "page_size=0", "page_size=101", "status=bogus", "service=EC2", "risk=extreme"],
)
def test_invalid_query_is_422_error_body(viewer: TestClient, query: str) -> None:
    response = viewer.get(f"/api/v1/changes?{query}")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["details"] and body["details"][0]["loc"][0] == "query"


def test_counts_ignore_the_status_filter(
    viewer: TestClient, make_change: ChangeFactory, store: FakeStore, make_incident
) -> None:
    make_change(E.analysis_written, risk=Risk.high)  # awaiting_review, high, open
    make_change(E.analysis_written, risk=Risk.low)  # awaiting_review
    accepted = make_change(E.analysis_written, E.accepted, risk=Risk.high)  # closed: not open
    linked = make_change()
    link(store, make_incident(), linked)
    make_change(service="iam")  # excluded by the service filter below

    body = viewer.get("/api/v1/changes?status=accepted&service=ec2").json()
    assert [item["id"] for item in body["items"]] == [accepted.id]
    assert body["total"] == 1
    counts = body["counts"]
    assert set(counts["by_status"]) == {status.value for status in ChangeStatus}
    assert counts["by_status"]["awaiting_review"] == 2
    assert counts["by_status"]["accepted"] == 1
    assert counts["by_status"]["detected"] == 1
    assert counts["by_status"]["reverted"] == 0
    assert counts["total"] == 4
    assert counts["awaiting_review"] == 2
    assert counts["high_risk_open"] == 1
    assert counts["linked_to_incident"] == 1
    assert counts["closed_last_7_days"] == 1


def test_closed_last_7_days_uses_the_decision_time(
    viewer: TestClient, make_change: ChangeFactory
) -> None:
    make_change(E.accepted, decided_at=NOW - timedelta(days=8))
    counts = viewer.get("/api/v1/changes").json()["counts"]
    assert counts["by_status"]["accepted"] == 1
    assert counts["closed_last_7_days"] == 0


# --- GET /api/v1/changes/{id} ----------------------------------------------------------------


def test_detail_includes_analysis_drift_remediation_and_links(
    viewer: TestClient, make_change: ChangeFactory, store: FakeStore, make_incident
) -> None:
    drift = {
        "tf_root": "terraform/demo-app/us-east-1",
        "tf_address": "aws_security_group.demo_app",
        "attribute": "ingress rules",
        "lines": [{"op": "added", "text": "22/tcp from 0.0.0.0/0", "note": "live only"}],
    }
    change = make_change(
        E.analysis_written,
        risk=Risk.high,
        drift=drift,
        request_parameters={"groupId": "sg-0001"},
        user_agent="console.amazonaws.com",
        title="SSH opened to the internet on the demo security group",
    )
    analysis = add_analysis(store, SubjectType.change, change.id, risk=Risk.high)
    store.remediations.insert(
        RemediationRow(
            subject_type=SubjectType.change,
            subject_id=change.id,
            kind=RemediationKind.revert_workflow,
            payload_json={"change_id": change.id},
        )
    )
    incident = make_incident()
    link(store, incident, change)

    response = viewer.get(f"/api/v1/changes/{change.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "SSH opened to the internet on the demo security group"
    assert body["status"] == "awaiting_review"
    assert body["request_parameters"] == {"groupId": "sg-0001"}
    assert body["drift"] == drift
    assert body["analysis"]["id"] == analysis.id
    assert body["analysis"]["agent_run"]["tool_calls"] == 0
    assert body["analysis"]["agent_run"]["model"] == "claude-haiku-5-5"
    assert body["remediation"]["kind"] == "revert_workflow"
    assert body["remediation"]["status"] == "proposed"
    assert body["linked_incidents"] == [
        {
            "incident": {
                "id": incident.id,
                "alertname": "KubePodCrashLooping",
                "namespace": "demo",
                "workload": "demo-app",
                "status": "open",
            },
            "confidence": "high",
        }
    ]
    summary = viewer.get("/api/v1/changes").json()["items"][0]
    assert summary["linked_incidents"][0]["id"] == incident.id


def test_detail_of_a_fresh_change_has_nulls(viewer: TestClient, make_change: ChangeFactory) -> None:
    body = viewer.get(f"/api/v1/changes/{make_change().id}").json()
    for key in ("analysis", "drift", "remediation", "decision", "decided_by", "reason"):
        assert body[key] is None, key


def test_detail_404(viewer: TestClient) -> None:
    response = viewer.get("/api/v1/changes/999")
    assert response.status_code == 404
    assert response.json() == {"code": "not_found", "message": "change 999 not found"}


@pytest.mark.parametrize("bad_id", ["0", "-1", "abc"])
def test_detail_bad_id_is_422(viewer: TestClient, bad_id: str) -> None:
    assert viewer.get(f"/api/v1/changes/{bad_id}").status_code == 422


# --- POST /api/v1/changes/{id}/accept --------------------------------------------------------


def test_accept_closes_the_change(
    admin: TestClient, make_change: ChangeFactory, store: FakeStore
) -> None:
    change = make_change(E.analysis_written)
    response = admin.post(f"/api/v1/changes/{change.id}/accept", json={"note": "Intended"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "accepted"
    assert body["decision"] == "accept"
    assert body["decided_by"] == "admin"
    assert body["reason"] == "Intended"
    assert body["decided_at"] is not None
    assert store.commits == 1


def test_accept_is_allowed_from_detected(admin: TestClient, make_change: ChangeFactory) -> None:
    change = make_change()
    response = admin.post(f"/api/v1/changes/{change.id}/accept", json={"note": "Known"})
    assert response.json()["status"] == "accepted"


@pytest.mark.parametrize(
    "body",
    [{}, {"note": ""}, {"note": "   "}, {"note": "x" * 2001}, {"note": "ok", "extra": 1}, None],
)
def test_accept_without_a_valid_note_is_422(
    admin: TestClient, make_change: ChangeFactory, body: dict | None
) -> None:
    change = make_change(E.analysis_written)
    response = admin.post(f"/api/v1/changes/{change.id}/accept", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"
    assert change.status is ChangeStatus.awaiting_review


def test_accept_with_malformed_json_is_400(admin: TestClient, make_change: ChangeFactory) -> None:
    change = make_change()
    response = admin.post(
        f"/api/v1/changes/{change.id}/accept",
        content=b"{not json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "bad_request"


@pytest.mark.parametrize(
    "events",
    [
        (E.analysis_written, E.revert_requested),
        (E.analysis_written, E.accepted),
        (E.analysis_written, E.revert_requested, E.revert_succeeded),
    ],
)
def test_accept_from_a_wrong_state_is_409(
    admin: TestClient, make_change: ChangeFactory, store: FakeStore, events
) -> None:
    change = make_change(*events)
    before = change.status
    response = admin.post(f"/api/v1/changes/{change.id}/accept", json={"note": "late"})
    assert response.status_code == 409
    assert response.json()["code"] == "invalid_state_transition"
    assert change.status is before
    assert store.commits == 0


def test_accept_unknown_change_is_404(admin: TestClient) -> None:
    assert admin.post("/api/v1/changes/42/accept", json={"note": "x"}).status_code == 404


def test_accept_needs_admin(
    anon: TestClient, viewer: TestClient, make_change: ChangeFactory
) -> None:
    change = make_change(E.analysis_written)
    url = f"/api/v1/changes/{change.id}/accept"
    assert anon.post(url, json={"note": "x"}).json()["code"] == "unauthorized"
    response = viewer.post(url, json={"note": "x"})
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"
    assert change.status is ChangeStatus.awaiting_review


def test_reads_need_a_session(anon: TestClient, make_change: ChangeFactory) -> None:
    change = make_change()
    for url in ("/api/v1/changes", f"/api/v1/changes/{change.id}"):
        response = anon.get(url)
        assert response.status_code == 401
        assert response.json()["code"] == "unauthorized"
    anon.cookies.set("ct_session", "forged")
    assert anon.get("/api/v1/changes").status_code == 401


# --- Later-issue stubs -----------------------------------------------------------------------


@pytest.mark.parametrize(("action", "issue"), [("revert", "#14"), ("codify", "Phase 2")])
def test_revert_and_codify_are_501_for_admins(
    admin: TestClient, viewer: TestClient, make_change: ChangeFactory, action: str, issue: str
) -> None:
    change = make_change(E.analysis_written)
    response = admin.post(f"/api/v1/changes/{change.id}/{action}", json={"note": None})
    assert response.status_code == 501
    assert response.json()["code"] == "not_implemented"
    assert issue in response.json()["message"]
    assert viewer.post(f"/api/v1/changes/{change.id}/{action}").status_code == 403
