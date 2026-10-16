"""Webhook → incident and change → Accept, over HTTP against Postgres."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import IncidentChangeRow, IncidentRow
from app.db.repositories import ChangeFilters
from app.db.sql import SqlUnitOfWork
from app.enums import Confidence, Risk, Via
from app.services import change_state
from app.services.change_state import ChangeEvent
from app.services.changes import NewChange, record_change
from tests.integration.conftest import WEBHOOK_TOKEN

pytestmark = pytest.mark.integration

ACCOUNT = "123456789012"
NOW = datetime.now(UTC).replace(microsecond=0)
AUTH = {"Authorization": f"Bearer {WEBHOOK_TOKEN}"}


def alert(fingerprint: str, status: str = "firing") -> dict:
    return {
        "status": status,
        "labels": {
            "alertname": "DemoAppAccessDenied",
            "severity": "critical",
            "namespace": "demo",
            "deployment": "demo-app",
        },
        "annotations": {"summary": "AccessDenied reading the demo bucket"},
        "startsAt": (NOW - timedelta(minutes=10)).isoformat(),
        "endsAt": "0001-01-01T00:00:00Z",
        "fingerprint": fingerprint,
    }


def deliver(client: TestClient, *alerts: dict) -> dict:
    body = {"version": "4", "status": alerts[0]["status"], "alerts": list(alerts)}
    response = client.post("/api/v1/alerts/webhook", json=body, headers=AUTH)
    assert response.status_code == 202, response.text
    return response.json()


def seed_change(engine: Engine, n: int = 1, *events: ChangeEvent, **fields) -> int:
    values = {
        "cloudtrail_event_id": f"evt-{n}",
        "event_time": NOW - timedelta(hours=n),
        "principal_arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/demo-app-developer/jdev",
        "session_name": "jdev",
        "via": Via.console,
        "region": "us-east-1",
        "service": "iam",
        "event_name": "DeleteRolePolicy",
        "resource_arn": f"arn:aws:iam::{ACCOUNT}:role/demo-app/demo-app-{n}",
        "resource_name": f"demo-app-{n}",
        "request_parameters": {"roleName": f"demo-app-{n}", "policyName": "s3-read"},
        **fields,
    }
    with SqlUnitOfWork(Session(engine)) as uow:
        row, created = record_change(uow, NewChange(**values))
        assert created
        for event in events:
            change_state.apply(row, event, now=NOW, actor="admin", reason="seeded")
        uow.commit()
        return row.id


def test_webhook_opens_dedupes_and_resolves(client: TestClient, clean_db: Engine) -> None:
    assert deliver(client, alert("fp-1"), alert("fp-2"))["opened"] == [1, 2]
    assert deliver(client, alert("fp-1")) == {"opened": [], "resolved": [], "ignored": 1}

    resolved = deliver(client, alert("fp-1", status="resolved"))
    assert resolved["resolved"] == [1]
    incident = client.get("/api/v1/incidents/1").json()
    assert incident["status"] == "resolved"
    assert incident["time_to_resolve_seconds"] >= 600
    assert incident["labels"]["deployment"] == "demo-app"

    # A new episode of the same alert gets a new incident.
    assert deliver(client, alert("fp-1"))["opened"] == [3]
    listing = client.get("/api/v1/incidents", params={"status": ["open"]}).json()
    assert [item["id"] for item in listing["items"]] == [3, 2]


def test_one_unresolved_incident_per_fingerprint_is_enforced_by_postgres(
    clean_db: Engine,
) -> None:
    row = {
        "fingerprint": "dup",
        "alertname": "X",
        "labels": {},
        "annotations": {},
        "opened_at": NOW,
    }
    with Session(clean_db) as session:
        session.add(IncidentRow(**row))
        session.commit()
        session.add(IncidentRow(**row))
        with pytest.raises(IntegrityError):
            session.commit()


def test_accept_flow(client: TestClient, clean_db: Engine) -> None:
    change_id = seed_change(clean_db, 1, ChangeEvent.analysis_written, risk=Risk.high)

    assert client.post(f"/api/v1/changes/{change_id}/accept", json={}).status_code == 422
    response = client.post(f"/api/v1/changes/{change_id}/accept", json={"note": "Planned"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["decision"], body["decided_by"], body["reason"]) == (
        "accepted",
        "accept",
        "admin",
        "Planned",
    )
    assert body["request_parameters"] == {"roleName": "demo-app-1", "policyName": "s3-read"}

    again = client.post(f"/api/v1/changes/{change_id}/accept", json={"note": "Twice"})
    assert again.status_code == 409
    with clean_db.connect() as connection:
        stored = connection.execute(
            text("SELECT status, reason FROM changes WHERE id = :id"), {"id": change_id}
        ).one()
    assert tuple(stored) == ("accepted", "Planned")

    counts = client.get("/api/v1/changes").json()["counts"]
    assert counts["by_status"]["accepted"] == 1
    assert counts["closed_last_7_days"] == 1
    assert counts["high_risk_open"] == 0


def test_change_log_filters_counts_and_links_in_sql(client: TestClient, clean_db: Engine) -> None:
    sg = seed_change(
        clean_db,
        1,
        ChangeEvent.analysis_written,
        service="ec2",
        event_name="AuthorizeSecurityGroupIngress",
        resource_name="demo-app_sg",
        risk=Risk.high,
    )
    iam = seed_change(clean_db, 2, via=Via.cli, risk=Risk.medium)
    seed_change(clean_db, 3, ChangeEvent.accepted, session_name="ops", principal_arn="arn:x")
    deliver(client, alert("fp-link"))
    with SqlUnitOfWork(Session(clean_db)) as uow:
        uow.links.add(IncidentChangeRow(incident_id=1, change_id=iam, confidence=Confidence.high))
        uow.commit()

    def ids(**params) -> list[int]:
        response = client.get("/api/v1/changes", params=params)
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()["items"]]

    assert ids() == [sg, iam, 3]
    assert ids(service="ec2") == [sg]
    assert ids(risk=["high", "medium"]) == [sg, iam]
    assert ids(via=["cli"]) == [iam]
    assert ids(principal="JDEV") == [sg, iam]
    assert ids(q="app_sg") == [sg]
    assert ids(q="_") == [sg]  # literal underscore, not a LIKE wildcard
    assert ids(status=["accepted"]) == [3]
    assert ids(since=(NOW - timedelta(hours=2)).isoformat()) == [sg, iam]
    assert ids(page=2, page_size=2) == [3]

    body = client.get("/api/v1/changes", params={"status": ["accepted"]}).json()
    assert body["total"] == 1
    assert body["counts"]["total"] == 3
    assert body["counts"]["awaiting_review"] == 1
    assert body["counts"]["by_status"]["detected"] == 1
    assert body["counts"]["high_risk_open"] == 1
    assert body["counts"]["linked_to_incident"] == 1

    detail = client.get(f"/api/v1/changes/{iam}").json()
    assert detail["linked_incidents"][0]["incident"]["alertname"] == "DemoAppAccessDenied"
    incident = client.get("/api/v1/incidents/1").json()
    assert incident["linked_changes"][0]["change"]["id"] == iam
    assert client.get(f"/api/v1/changes/{iam}/evidence").json() == {"items": []}

    with SqlUnitOfWork(Session(clean_db)) as uow:
        assert uow.changes.get_by_event_id("evt-2").id == iam
        rows, total = uow.changes.list(ChangeFilters(), offset=0, limit=1)
        assert (len(rows), total) == (1, 3)


def test_probes_and_intake_health(client: TestClient) -> None:
    assert client.get("/readyz").json() == {"status": "ok", "checks": {"database": "ok"}}
    health = client.get("/api/v1/intake/health").json()
    assert health["state"] == "down"
    assert health["last_event_at"] is None
    assert "consoletrack_intake_state" in client.get("/metrics").text
