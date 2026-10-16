from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from app.enums import Role
from app.main import create_app
from app.schemas import User
from app.services.intake_health import IntakeSnapshot
from app.settings import IntakeMode, Settings
from tests.unit.conftest import NOW, VIEWER_TOKEN, ChangeFactory
from tests.unit.fakes import FakeStore


class StaticIntake:
    def __init__(self, snapshot: IntakeSnapshot) -> None:
        self._snapshot = snapshot

    def snapshot(self, uow) -> IntakeSnapshot:
        return self._snapshot


HEALTHY = IntakeSnapshot(
    last_canary_at=NOW - timedelta(minutes=5),
    queue_backlog=2,
    oldest_message_age_seconds=30,
    dead_letter_count=0,
    trail_logging=True,
)


def metrics(client: TestClient) -> dict[str, float]:
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    values = {}
    for family in text_string_to_metric_families(response.text):
        for sample in family.samples:
            key = sample.name + "".join(f"{{{k}={v}}}" for k, v in sample.labels.items())
            values[key] = sample.value
    return values


def test_intake_health_before_the_worker_reports_is_down(
    viewer: TestClient, make_change: ChangeFactory
) -> None:
    make_change(detected_at=NOW - timedelta(minutes=3))
    response = viewer.get("/api/v1/intake/health")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "down"
    assert body["reasons"]
    assert body["region"] == "us-east-1"
    assert datetime.fromisoformat(body["last_event_at"]) == NOW - timedelta(minutes=3)
    assert body["last_canary_at"] is None
    assert body["queue_backlog"] is None
    assert body["grafana_url"] is None


def test_intake_health_live_from_a_source(store: FakeStore) -> None:
    app = create_app(
        Settings(grafana_url="http://localhost:3000/d/intake"),
        uow_factory=store.uow,
        intake_status=StaticIntake(HEALTHY),
        clock=lambda: NOW,
    )
    app.state.sessions.put(VIEWER_TOKEN, _viewer())
    client = TestClient(app)
    client.cookies.set("ct_session", VIEWER_TOKEN)
    body = client.get("/api/v1/intake/health").json()
    assert body["state"] == "live"
    assert body["reasons"] == []
    assert body["queue_backlog"] == 2
    assert body["trail_logging"] is True
    assert body["grafana_url"] == "http://localhost:3000/d/intake"


def test_intake_health_replay_mode(store: FakeStore) -> None:
    app = create_app(Settings(intake_mode=IntakeMode.replay), uow_factory=store.uow)
    app.state.sessions.put("t", _viewer())
    client = TestClient(app)
    client.cookies.set("ct_session", "t")
    assert client.get("/api/v1/intake/health").json()["state"] == "replay"


def test_intake_health_needs_a_session(anon: TestClient) -> None:
    assert anon.get("/api/v1/intake/health").status_code == 401


def test_metrics_before_anything_reports(anon: TestClient) -> None:
    values = metrics(anon)
    assert values["consoletrack_intake_last_event_timestamp_seconds"] == 0
    assert values["consoletrack_canary_last_seen_timestamp_seconds"] == 0
    assert math.isnan(values["consoletrack_sqs_oldest_message_age_seconds"])
    assert math.isnan(values["consoletrack_dlq_messages"])
    assert values["consoletrack_intake_state{state=down}"] == 1
    assert values["consoletrack_intake_state{state=live}"] == 0


def test_metrics_follow_the_intake(store: FakeStore, make_change: ChangeFactory) -> None:
    make_change(detected_at=NOW - timedelta(minutes=1))
    client = TestClient(
        create_app(
            Settings(),
            uow_factory=store.uow,
            intake_status=StaticIntake(HEALTHY),
            clock=lambda: NOW,
        )
    )
    values = metrics(client)
    assert (
        values["consoletrack_intake_last_event_timestamp_seconds"]
        == (NOW - timedelta(minutes=1)).timestamp()
    )
    assert values["consoletrack_canary_last_seen_timestamp_seconds"] == (
        HEALTHY.last_canary_at.timestamp()
    )
    assert values["consoletrack_sqs_oldest_message_age_seconds"] == 30
    assert values["consoletrack_dlq_messages"] == 0
    assert values["consoletrack_intake_state{state=live}"] == 1
    assert sum(v for k, v in values.items() if k.startswith("consoletrack_intake_state")) == 1


def test_healthz(anon: TestClient) -> None:
    assert anon.get("/healthz").json() == {"status": "ok"}


def test_readyz_ok(anon: TestClient) -> None:
    response = anon.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok"}}


def test_readyz_db_down(store: FakeStore) -> None:
    def broken() -> None:
        raise ConnectionError("connection refused to db:5432 password=hunter2")

    client = TestClient(create_app(Settings(), uow_factory=store.uow, db_check=broken))
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "error", "checks": {"database": "error"}}
    assert "hunter2" not in response.text


def test_create_app_needs_a_database_url_without_a_factory() -> None:
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app(Settings(database_url=None))


# --- Auth placeholder ------------------------------------------------------------------------


def _viewer() -> User:
    return User(username="viewer", role=Role.viewer)


def test_me_returns_the_session_user(viewer: TestClient, anon: TestClient) -> None:
    assert viewer.get("/api/v1/auth/me").json() == {"username": "viewer", "role": "viewer"}
    assert anon.get("/api/v1/auth/me").status_code == 401


def test_login_and_logout_are_501(anon: TestClient, viewer: TestClient) -> None:
    login = anon.post("/api/v1/auth/login", json={"username": "admin", "password": "x"})
    assert login.status_code == 501
    assert viewer.post("/api/v1/auth/logout").status_code == 501
    assert anon.post("/api/v1/auth/logout").status_code == 401


def test_stats_is_phase_2(viewer: TestClient) -> None:
    response = viewer.get("/api/v1/stats")
    assert response.status_code == 501
    assert "Phase 2" in response.json()["message"]


def test_github_webhook_is_501_after_validation(anon: TestClient) -> None:
    event = {
        "action": "completed",
        "workflow_run": {
            "id": 1,
            "path": ".github/workflows/revert.yaml",
            "html_url": "https://github.com/jennasrunbooks/consoletrack/actions/runs/1",
            "status": "completed",
            "conclusion": "success",
        },
    }
    headers = {"X-GitHub-Event": "workflow_run", "X-GitHub-Delivery": "d-1"}
    url = "/api/v1/integrations/github/workflow-run"
    assert anon.post(url, json=event, headers=headers).status_code == 501
    assert anon.post(url, json=event).status_code == 422  # required headers


def test_unknown_route_is_an_error_body(anon: TestClient) -> None:
    response = anon.get("/api/v1/nope")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
