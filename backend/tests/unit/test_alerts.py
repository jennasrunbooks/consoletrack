from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.enums import IncidentDecision, IncidentStatus
from app.services.alerts import workload_of
from tests.unit.conftest import NOW, WEBHOOK_TOKEN
from tests.unit.fakes import FakeStore

URL = "/api/v1/alerts/webhook"
AUTH = {"Authorization": f"Bearer {WEBHOOK_TOKEN}"}
STARTS = (NOW - timedelta(minutes=30)).isoformat()


def alert(fingerprint: str, status: str = "firing", **labels: str) -> dict[str, Any]:
    return {
        "status": status,
        "labels": {
            "alertname": "KubePodCrashLooping",
            "severity": "critical",
            "namespace": "demo",
            "deployment": "demo-app",
            **labels,
        },
        "annotations": {"summary": "demo-app is crash looping"},
        "startsAt": STARTS,
        # Alertmanager sends the Go zero time for alerts that are still firing.
        "endsAt": "0001-01-01T00:00:00Z" if status == "firing" else NOW.isoformat(),
        "generatorURL": "http://prometheus/graph",
        "fingerprint": fingerprint,
    }


def payload(*alerts: dict[str, Any], status: str = "firing", **extra: Any) -> dict[str, Any]:
    return {
        "version": "4",
        "groupKey": '{}:{alertname="KubePodCrashLooping"}',
        "truncatedAlerts": 0,
        "status": status,
        "receiver": "consoletrack",
        "groupLabels": {"alertname": "KubePodCrashLooping"},
        "commonLabels": {},
        "commonAnnotations": {},
        "externalURL": "http://alertmanager",
        "alerts": list(alerts),
        **extra,
    }


def post(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post(URL, json=body, headers=AUTH)
    assert response.status_code == 202, response.text
    return response.json()


def test_firing_opens_an_incident(anon: TestClient, store: FakeStore) -> None:
    result = post(anon, payload(alert("aaa")))
    assert result == {"opened": [1], "resolved": [], "ignored": 0}
    (incident,) = store.incidents.rows
    assert incident.status is IncidentStatus.open
    assert incident.alertname == "KubePodCrashLooping"
    assert (incident.severity, incident.namespace, incident.workload) == (
        "critical",
        "demo",
        "demo-app",
    )
    assert incident.opened_at.isoformat() == STARTS
    assert incident.annotations == {"summary": "demo-app is crash looping"}
    assert store.commits == 1


def test_refiring_alert_reuses_the_open_incident(anon: TestClient, store: FakeStore) -> None:
    post(anon, payload(alert("aaa")))
    assert post(anon, payload(alert("aaa"))) == {"opened": [], "resolved": [], "ignored": 1}
    assert len(store.incidents.rows) == 1


def test_resolved_resolves_the_matching_incident(anon: TestClient, store: FakeStore) -> None:
    post(anon, payload(alert("aaa"), alert("bbb")))
    result = post(anon, payload(alert("aaa", status="resolved"), status="resolved"))
    assert result == {"opened": [], "resolved": [1], "ignored": 0}
    first, second = store.incidents.rows
    assert first.status is IncidentStatus.resolved
    assert first.resolved_at == NOW
    assert first.decision is IncidentDecision.resolved
    assert first.decided_by == "alertmanager"
    assert second.status is IncidentStatus.open


def test_resolved_uses_ends_at_when_earlier_than_now(anon: TestClient, store: FakeStore) -> None:
    post(anon, payload(alert("aaa")))
    resolved = alert("aaa", status="resolved")
    ended = NOW - timedelta(minutes=2)
    resolved["endsAt"] = ended.isoformat()
    post(anon, payload(resolved, status="resolved"))
    assert store.incidents.rows[0].resolved_at == ended


def test_a_new_episode_after_resolution_opens_a_new_incident(
    anon: TestClient, store: FakeStore
) -> None:
    post(anon, payload(alert("aaa")))
    post(anon, payload(alert("aaa", status="resolved"), status="resolved"))
    assert post(anon, payload(alert("aaa")))["opened"] == [2]
    assert [row.status for row in store.incidents.rows] == [
        IncidentStatus.resolved,
        IncidentStatus.open,
    ]


def test_grouped_delivery_handles_each_alert_on_its_own_status(
    anon: TestClient, store: FakeStore
) -> None:
    post(anon, payload(alert("old")))
    result = post(
        anon,
        payload(
            alert("old", status="resolved"),
            alert("new"),
            alert("unknown", status="resolved"),
            alert("new"),  # duplicate within one delivery
        ),
    )
    assert result == {"opened": [2], "resolved": [1], "ignored": 2}


def test_unknown_fields_are_ignored_and_labels_may_be_sparse(
    anon: TestClient, store: FakeStore
) -> None:
    sparse = {
        "status": "firing",
        "labels": {},
        "annotations": {},
        "startsAt": STARTS,
        "fingerprint": "zzz",
        "silenceURL": "ignored",
    }
    post(anon, payload(sparse, futureField=True))
    incident = store.incidents.rows[0]
    assert incident.alertname == "unknown"
    assert (incident.severity, incident.namespace, incident.workload) == (None, None, None)


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        ({"workload": "w", "deployment": "d"}, "w"),
        ({"deployment": "d", "pod": "p"}, "d"),
        ({"statefulset": "s"}, "s"),
        ({"pod": "p-123"}, "p-123"),
        ({"deployment": ""}, None),
        ({}, None),
    ],
)
def test_workload_label_precedence(labels: dict[str, str], expected: str | None) -> None:
    assert workload_of(labels) == expected


@pytest.mark.parametrize(
    "body",
    [
        payload(alert("a"), version="3"),
        payload(alert("a"), status="pending"),
        payload({**alert("a"), "status": "silenced"}),
        payload({**alert("a"), "fingerprint": None}),
        {"version": "4", "status": "firing"},
    ],
)
def test_invalid_payload_is_422(anon: TestClient, body: dict[str, Any]) -> None:
    response = anon.post(URL, json=body, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Basic {WEBHOOK_TOKEN}"}],
)
def test_webhook_needs_the_bearer_token(
    anon: TestClient, store: FakeStore, headers: dict[str, str]
) -> None:
    response = anon.post(URL, json=payload(alert("a")), headers=headers)
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"
    assert store.incidents.rows == []


def test_webhook_fails_closed_without_a_configured_token(store: FakeStore) -> None:
    from app.main import create_app
    from app.settings import Settings

    client = TestClient(create_app(Settings(alert_webhook_token=None), uow_factory=store.uow))
    response = client.post(URL, json=payload(alert("a")), headers={"Authorization": "Bearer "})
    assert response.status_code == 401
    assert store.incidents.rows == []
