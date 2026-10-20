"""Webhook → queued incident → worker (fake model, fake k8s-mcp) → analysis, on Postgres.

Checks the SQL side of the triage queue: `FOR UPDATE SKIP LOCKED` lets two workers claim
different incidents, and a full run writes the run, evidence, analysis, and proposal rows that
the incident API then reads back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.agents.runner import AgentRunner, RunLimits
from app.agents.triage import IncidentTriage
from app.db.session import make_uow_factory
from app.db.sql import SqlUnitOfWork
from app.services.policy_gate import ManualPolicyGate
from app.settings import Settings
from tests.integration.conftest import WEBHOOK_TOKEN
from tests.unit.agent_fakes import FakeModelClient, call, k8s_oom, submit, toolbox_factory, turn

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

AUTH = {"Authorization": f"Bearer {WEBHOOK_TOKEN}"}


def fire(client: TestClient, *fingerprints: str) -> list[int]:
    now = datetime.now(UTC)
    payload = {
        "version": "4",
        "status": "firing",
        "receiver": "consoletrack",
        "groupKey": "{}:{}",
        "alerts": [
            {
                "status": "firing",
                "fingerprint": fp,
                "labels": {"alertname": "DemoAppHighErrorRate", "namespace": "demo"},
                "annotations": {},
                "startsAt": (now - timedelta(minutes=2)).isoformat(),
            }
            for fp in fingerprints
        ],
    }
    response = client.post("/api/v1/alerts/webhook", json=payload, headers=AUTH)
    assert response.status_code == 202, response.text
    return response.json()["opened"]


def test_two_workers_claim_different_incidents(client: TestClient, clean_db: Engine) -> None:
    first, second = fire(client, "fp-a", "fp-b")
    later = datetime.now(UTC) + timedelta(seconds=5)
    with Session(clean_db) as a, Session(clean_db) as b, Session(clean_db) as c:
        claimed_a = SqlUnitOfWork(a).incidents.next_for_triage(later)
        claimed_b = SqlUnitOfWork(b).incidents.next_for_triage(later)
        assert claimed_a is not None and claimed_b is not None
        assert {claimed_a.id, claimed_b.id} == {first, second}
        # Both rows are locked by the open transactions above.
        assert SqlUnitOfWork(c).incidents.next_for_triage(later) is None


async def test_oom_triage_writes_every_row(client: TestClient, clean_db: Engine) -> None:
    (incident_id,) = fire(client, "fp-oom")
    uow_factory = make_uow_factory(clean_db)
    clock = lambda: datetime.now(UTC) + timedelta(seconds=5)  # noqa: E731
    model = FakeModelClient(
        [
            turn(call("get_pods", {"namespace": "demo"})),
            turn(call("get_workload", {"namespace": "demo", "kind": "Deployment", "name": "x"})),
            submit(
                flags=["insufficient_evidence"],
                proposed_remediation={
                    "kind": "set_memory_limit",
                    "namespace": "demo",
                    "deployment": "demo-app",
                    "container": "demo-app",
                    "memory_limit": "256Mi",
                    "reason": "OOMKilled at 128Mi.",
                },
            ),
        ]
    )
    triage = IncidentTriage(
        uow_factory=uow_factory,
        runner=AgentRunner(model, uow_factory, limits=RunLimits(), clock=clock),
        gate=ManualPolicyGate(),
        settings=Settings(k8s_mcp_url="http://k8s-mcp:8080/mcp"),
        toolbox_factory=toolbox_factory(k8s_oom()),
        clock=clock,
    )
    assert await triage.run_next() is True
    assert await triage.run_next() is False

    incident = client.get(f"/api/v1/incidents/{incident_id}").json()
    assert incident["status"] == "awaiting_review"
    assert incident["analysis"]["agent_run"]["outcome"] == "succeeded"
    assert incident["analysis"]["agent_run"]["tool_calls"] == 2
    assert incident["remediation"]["kind"] == "set_memory_limit"
    assert incident["remediation"]["status"] == "proposed"
    evidence = client.get(f"/api/v1/incidents/{incident_id}/evidence").json()
    assert [item["tool_name"] for item in evidence["items"]] == ["get_pods", "get_workload"]
