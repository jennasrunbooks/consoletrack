from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import SESSION_COOKIE
from app.db.models import AgentRunRow, AnalysisRow, ChangeRow, IncidentChangeRow, IncidentRow
from app.enums import AgentRunOutcome, Confidence, Role, SubjectType, Via
from app.main import create_app
from app.schemas import User
from app.services import change_state
from app.services.change_state import ChangeEvent
from app.services.changes import NewChange, record_change
from app.settings import Settings
from tests.unit.fakes import FakeStore

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
WEBHOOK_TOKEN = "test-webhook-token"
ADMIN_PASSWORD = "admin-test-password"
VIEWER_PASSWORD = "viewer-test-password"
# Low-cost argon2id parameters keep the tests fast; production hashes use the library defaults.
_fast_hasher = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
ADMIN_HASH = _fast_hasher.hash(ADMIN_PASSWORD)
VIEWER_HASH = _fast_hasher.hash(VIEWER_PASSWORD)
ACCOUNT = "123456789012"  # placeholder account ID (AGENTS.md → Hard rules)


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=None,
        alert_webhook_token=WEBHOOK_TOKEN,
        grafana_url=None,
        admin_password_hash=ADMIN_HASH,
        viewer_password_hash=VIEWER_HASH,
    )


@pytest.fixture
def app(store: FakeStore, settings: Settings) -> FastAPI:
    return create_app(settings, uow_factory=store.uow, clock=lambda: NOW)


def session_token(app: FastAPI, username: str) -> str:
    """A valid signed `ct_session` value, as login would issue it at NOW."""
    role = Role.admin if username == "admin" else Role.viewer
    return app.state.sessions.issue(User(username=username, role=role), NOW)


@pytest.fixture
def anon(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def viewer(app: FastAPI) -> TestClient:
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, session_token(app, "viewer"))
    return client


@pytest.fixture
def admin(app: FastAPI) -> TestClient:
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, session_token(app, "admin"))
    return client


ChangeFactory = Callable[..., ChangeRow]


@pytest.fixture
def make_change(store: FakeStore) -> ChangeFactory:
    """Record a change through the service, then walk it to `events` via the state machine."""
    counter = iter(range(1, 10_000))

    def make(*events: ChangeEvent, **fields: Any) -> ChangeRow:
        n = next(counter)
        values: dict[str, Any] = {
            "cloudtrail_event_id": f"evt-{n}",
            "event_time": NOW - timedelta(hours=n),
            "principal_arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/demo-app-developer/jdev",
            "session_name": "jdev",
            "via": Via.console,
            "region": "us-east-1",
            "service": "ec2",
            "event_name": "AuthorizeSecurityGroupIngress",
            "resource_arn": f"arn:aws:ec2:us-east-1:{ACCOUNT}:security-group/sg-{n:04d}",
            "resource_name": "demo-app-sg",
        }
        values.update({k: v for k, v in fields.items() if k in NewChange.__dataclass_fields__})
        row, _ = record_change(store.uow(), NewChange(**values))
        for event in events:
            change_state.apply(row, event, now=NOW, actor="admin", reason="seeded")
        for key, value in fields.items():
            if key not in NewChange.__dataclass_fields__:
                setattr(row, key, value)  # e.g. detected_at, title, decided_at
        return row

    return make


@pytest.fixture
def make_incident(store: FakeStore) -> Callable[..., IncidentRow]:
    counter = iter(range(1, 10_000))

    def make(**fields: Any) -> IncidentRow:
        n = next(counter)
        values: dict[str, Any] = {
            "fingerprint": f"fp-{n}",
            "alertname": "KubePodCrashLooping",
            "severity": "critical",
            "namespace": "demo",
            "workload": "demo-app",
            "labels": {"alertname": "KubePodCrashLooping"},
            "annotations": {},
            "opened_at": NOW - timedelta(hours=n),
        }
        values.update(fields)
        return store.incidents.insert(IncidentRow(**values))

    return make


def link(store: FakeStore, incident: IncidentRow, change: ChangeRow) -> None:
    store.links.insert(
        IncidentChangeRow(incident_id=incident.id, change_id=change.id, confidence=Confidence.high)
    )


def add_analysis(
    store: FakeStore, subject_type: SubjectType, subject_id: int, **fields: Any
) -> AnalysisRow:
    run = store.agent_runs.insert(
        AgentRunRow(
            subject_type=subject_type,
            subject_id=subject_id,
            model="claude-haiku-5-5",
            started_at=NOW - timedelta(minutes=5),
            finished_at=NOW - timedelta(minutes=4),
            input_tokens=1200,
            output_tokens=300,
            outcome=AgentRunOutcome.succeeded,
        )
    )
    values: dict[str, Any] = {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "agent_run_id": run.id,
        "summary": "jdev opened port 22 to 0.0.0.0/0 from the console.",
        "confidence": Confidence.high,
    }
    values.update(fields)
    return store.analyses.insert(AnalysisRow(**values))
