"""The incident journey's telemetry: one trace from the alert to resolution, and nothing private
in it (docs/observability.md → Privacy).

The journey runs on fakes: the firing webhook, the worker's triage with the scripted model and
an in-process k8s-mcp, the policy gate (auto for the OOM fix), the backend's apply loop, and
the resolved webhook. Its inputs carry everything that must never reach telemetry: the webhook's
bearer token, a session cookie, a client IP, an ARN and account ID in the alert, and access keys
in a tool result.

The check fails when any exported attribute key is outside the allowlist or any value looks
like a credential, IP address, account ID, or ARN. It also runs on ConsoleTrack's own spans as
the code made them, before the exporter's allowlist, so code that puts a header, a prompt, or an
IP on a span fails here even though the exporter would drop the key.
"""

from __future__ import annotations

import io
import json
import logging
import re
from collections.abc import Iterable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import ReadableSpan

from app import telemetry
from app.agents.runner import AgentRunner, RunLimits
from app.agents.triage import IncidentTriage
from app.api.deps import SESSION_COOKIE
from app.enums import IncidentStatus, RemediationStatus
from app.settings import Settings
from app.telemetry import ALLOWED_ATTRIBUTES, METRIC_LABELS, tracer
from tests.telemetry_recorder import Recorded
from tests.unit.agent_fakes import FakeModelClient, call, k8s_oom, submit, toolbox_factory, turn
from tests.unit.conftest import NOW, WEBHOOK_TOKEN, session_token
from tests.unit.fakes import FakeStore
from tests.unit.remediation_fakes import OOM_PROPOSAL, Clock, FakeCluster
from tests.unit.test_policy_gate import gate
from tests.unit.test_remediation_apply import applier

pytestmark = pytest.mark.anyio

CLIENT_IP = "198.51.100.7"
ROLE_ARN = "arn:aws:iam::123456789012:role/demo-app"
ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
# What a pod log could hold; the agent reads it as evidence, telemetry never sees it.
LEAKY_LOG = (
    f"boto: using {ACCESS_KEY} for {ROLE_ARN}\n"
    "ANTHROPIC_API_KEY=sk-ant-api03-not-a-real-key ASIAIOSFODNN7EXAMPLE"
)
POD = "demo-app-7d9f8b6c5-x2x4z"
SCRIPT = [
    turn(call("get_pods", {"namespace": "demo", "selector": "app=demo-app"}, id="t1")),
    turn(call("get_workload", {"namespace": "demo", "kind": "Deployment", "name": "demo-app"})),
    turn(call("get_pod_logs", {"namespace": "demo", "pod": POD, "previous": True, "tail": 50})),
    submit(proposed_remediation=OOM_PROPOSAL),
]

SECRET_PATTERNS = {
    "AWS access key": re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"),
    "account ID": re.compile(r"(?<!\d)\d{12}(?!\d)"),
    "ARN": re.compile(r"arn:aws"),
    "session cookie": re.compile(SESSION_COOKIE),
    "Anthropic key": re.compile(r"sk-ant-"),
    "bearer token": re.compile(r"(?i)bearer\s|" + re.escape(WEBHOOK_TOKEN)),
    "IP address": re.compile(r"\b\d{1,3}(\.\d{1,3}){3}\b"),
}

# The journey's spans, all in one trace.
JOURNEY = {
    "alert.receive",
    "incident.open",
    "triage.run",
    "mcp.call",
    "remediation.gate",
    "remediation.validate",
    "remediation.apply",
    "remediation.verify",
    "incident.resolve",
}


def leaks(text: str) -> list[str]:
    return [name for name, pattern in SECRET_PATTERNS.items() if pattern.search(text)]


def span_problems(spans: Iterable[ReadableSpan]) -> list[str]:
    """Every attribute key outside the allowlist, and every value that looks private."""
    problems: list[str] = []
    for span in spans:
        problems += [f"{span.name}: {name} in the span name" for name in leaks(span.name)]
        attribute_sets = [("", span.attributes)]
        attribute_sets += [(f" event {e.name}", e.attributes) for e in span.events]
        attribute_sets += [(" link", link.attributes) for link in span.links]
        for where, attributes in attribute_sets:
            for key, value in (attributes or {}).items():
                if key not in ALLOWED_ATTRIBUTES:
                    problems.append(f"{span.name}{where}: {key} is not allowlisted")
                problems += [f"{span.name}{where}: {key} holds a {n}" for n in leaks(str(value))]
        if span.status.description:
            problems += [f"{span.name}: status holds a {n}" for n in leaks(span.status.description)]
    return problems


def ours(spans: Iterable[ReadableSpan]) -> list[ReadableSpan]:
    """ConsoleTrack's own spans (not FastAPI's or FastMCP's), as the code made them."""
    return [s for s in spans if getattr(s.instrumentation_scope, "name", None) == "consoletrack"]


def metric_points(recorded: Recorded) -> dict[str, list[Any]]:
    points: dict[str, list[Any]] = {}
    data = recorded.metrics.get_metrics_data()
    for resource in data.resource_metrics if data else []:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                points.setdefault(metric.name, []).extend(metric.data.data_points)
    return points


@pytest.fixture
def log_lines(recorded: Recorded) -> Iterator[io.StringIO]:
    """The root logger as `telemetry.configure_logging` sets it up: JSON lines (captured here)
    and the OTel log pipeline (the recorder)."""
    stream = io.StringIO()
    json_handler = logging.StreamHandler(stream)
    json_handler.setFormatter(telemetry.JsonFormatter())
    otel_handler = telemetry.OtelLogHandler(recorded.logger_provider)
    root = logging.getLogger()
    level = root.level
    root.addHandler(json_handler)
    root.addHandler(otel_handler)
    root.setLevel(logging.INFO)
    yield stream
    root.removeHandler(json_handler)
    root.removeHandler(otel_handler)
    root.setLevel(level)


def webhook(client: TestClient, status: str, **alert: Any) -> dict[str, Any]:
    labels = {
        "alertname": "DemoAppHighErrorRate",
        "namespace": "demo",
        "deployment": "demo-app",
        "severity": "critical",
    }
    payload = {
        "version": "4",
        "status": status,
        "receiver": "consoletrack",
        "groupKey": "{}:{alertname}",
        "alerts": [
            {
                "status": status,
                "fingerprint": "fp-oom",
                "labels": labels,
                "annotations": {"description": f"{ROLE_ARN} denied; key {ACCESS_KEY}"},
                "startsAt": (NOW - timedelta(minutes=3)).isoformat(),
                **alert,
            }
        ],
    }
    response = client.post(
        "/api/v1/alerts/webhook",
        json=payload,
        headers={
            "Authorization": f"Bearer {WEBHOOK_TOKEN}",
            "X-Forwarded-For": CLIENT_IP,
            "User-Agent": f"Alertmanager/0.28 ({CLIENT_IP})",
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


async def run_journey(store: FakeStore, app: FastAPI) -> None:
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, session_token(app, "admin"))
    (incident_id,) = webhook(client, "firing")["opened"]

    clock = Clock()
    triage = IncidentTriage(
        uow_factory=store.uow,
        runner=AgentRunner(
            FakeModelClient(SCRIPT), store.uow, limits=RunLimits(), clock=lambda: NOW
        ),
        gate=gate(store),
        settings=Settings(k8s_mcp_url="http://k8s-mcp:8080/mcp"),
        toolbox_factory=toolbox_factory(k8s_oom(log_lines=LEAKY_LOG)),
        clock=lambda: NOW,
    )
    decision = await triage.run_next()
    assert decision is True
    assert applier(store, FakeCluster(clock=clock), clock).run_next()
    (remediation,) = store.remediations.rows
    assert remediation.status is RemediationStatus.succeeded

    resolved = webhook(client, "resolved", endsAt=NOW.isoformat())
    assert resolved["resolved"] == [incident_id]
    assert store.incidents.rows[0].status is IncidentStatus.resolved


async def test_the_journey_is_one_trace_from_alert_to_resolution(
    store: FakeStore, app: FastAPI, recorded: Recorded
) -> None:
    await run_journey(store, app)
    spans = [s for s in recorded.exported.get_finished_spans() if s.name in JOURNEY]
    assert {s.name for s in spans} == JOURNEY
    assert len({s.context.trace_id for s in spans}) == 1
    by_name: dict[str, list[ReadableSpan]] = {}
    for span in spans:
        by_name.setdefault(span.name, []).append(span)
    assert len(by_name["mcp.call"]) == 3
    assert len(by_name["alert.receive"]) == 2  # firing, then resolved

    def parent(name: str) -> str:
        (span,) = by_name[name]
        assert span.parent is not None
        return next(s.name for s in spans if s.context.span_id == span.parent.span_id)

    # The stored traceparents: the incident's (incident.open), then the remediation's (the gate).
    assert parent("incident.open") == "alert.receive"
    assert parent("triage.run") == "incident.open"
    assert parent("remediation.gate") == "triage.run"
    assert {parent(n) for n in ("remediation.validate", "remediation.apply")} == {
        "remediation.gate"
    }
    assert parent("remediation.verify") == "remediation.gate"
    assert parent("incident.resolve") == "alert.receive"

    (opened,) = by_name["incident.open"]
    assert opened.attributes["incident.severity"] == "critical"
    (run,) = by_name["triage.run"]
    assert run.attributes["triage.tool_calls"] == 3
    assert run.attributes["triage.outcome"] == "succeeded"
    assert run.attributes["triage.escalated"] is False
    assert {s.attributes["mcp.tool"] for s in by_name["mcp.call"]} == {
        "get_pods",
        "get_workload",
        "get_pod_logs",
    }
    (gated,) = by_name["remediation.gate"]
    assert gated.attributes["remediation.mode"] == "auto"
    (verify,) = by_name["remediation.verify"]
    assert verify.attributes["remediation.result"] == "succeeded"
    (resolve,) = by_name["incident.resolve"]
    assert resolve.attributes["incident.resolution"] == "auto_fixed"
    assert resolve.attributes["incident.time_to_resolve_s"] == 180.0


async def test_nothing_private_reaches_telemetry(
    store: FakeStore, app: FastAPI, recorded: Recorded, log_lines: io.StringIO
) -> None:
    await run_journey(store, app)

    # ConsoleTrack's spans as made, and every span (FastAPI's too) as exported.
    assert span_problems(ours(recorded.raw.get_finished_spans())) == []
    exported = recorded.exported.get_finished_spans()
    assert any(s.name.startswith("POST /api/v1/alerts/webhook") for s in exported)
    assert span_problems(exported) == []

    records = [r.log_record for r in recorded.logs.get_finished_logs()]
    assert records, "the journey logs through the OTel pipeline"
    for record in records:
        assert set(record.attributes or {}) <= ALLOWED_ATTRIBUTES, record.attributes
        assert leaks(f"{record.body} {dict(record.attributes or {})}") == [], record.body
    for line in log_lines.getvalue().splitlines():
        # The hex IDs are random: one can hold 12 digits in a row, like an account ID.
        entry = {k: v for k, v in json.loads(line).items() if k not in {"trace_id", "span_id"}}
        assert leaks(json.dumps(entry)) == [], line

    points = metric_points(recorded)
    for name, data_points in points.items():
        for point in data_points:
            assert set(point.attributes) <= METRIC_LABELS, (name, point.attributes)
            assert leaks(json.dumps(dict(point.attributes))) == [], (name, point.attributes)


async def test_logs_carry_the_trace_id(
    store: FakeStore, app: FastAPI, recorded: Recorded, log_lines: io.StringIO
) -> None:
    await run_journey(store, app)
    (run,) = [s for s in recorded.exported.get_finished_spans() if s.name == "triage.run"]
    trace_id = f"{run.context.trace_id:032x}"
    lines = [json.loads(line) for line in log_lines.getvalue().splitlines()]
    started = next(line for line in lines if line["event"].startswith("triage started"))
    assert started["trace_id"] == trace_id
    assert {"ts", "level", "logger", "event", "span_id"} <= set(started)
    exported = next(
        r.log_record
        for r in recorded.logs.get_finished_logs()
        if str(r.log_record.body).startswith("triage started")
    )
    assert f"{exported.trace_id:032x}" == trace_id


async def test_the_journey_records_the_dashboard_metrics(
    store: FakeStore, app: FastAPI, recorded: Recorded
) -> None:
    await run_journey(store, app)
    points = metric_points(recorded)

    def total(name: str, **labels: str) -> float:
        return sum(
            getattr(p, "value", None) if hasattr(p, "value") else p.count
            for p in points.get(name, [])
            if all(p.attributes.get(k) == v for k, v in labels.items())
        )

    assert total("ct_incidents_opened_total", severity="critical") == 1
    assert total("ct_incidents_resolved_total", resolution="auto_fixed") == 1
    assert total("ct_time_to_resolve_seconds") == 1
    assert total("ct_triage_duration_seconds") == 1
    assert total("ct_triage_tool_calls") == 1
    assert total("ct_remediations_total", kind="set_memory_limit", mode="auto", result="succeeded")
    assert (
        total(
            "ct_http_requests_total",
            route="/api/v1/alerts/webhook",
            method="POST",
            status_code="202",
        )
        == 2
    )


def test_request_metrics_use_the_route_template(viewer: TestClient, recorded: Recorded) -> None:
    viewer.get("/api/v1/incidents/41")
    viewer.get("/api/v1/no-such-route")
    viewer.get("/healthz")
    points = metric_points(recorded)["ct_http_requests_total"]
    assert {(p.attributes["route"], p.attributes["status_code"]) for p in points} == {
        ("/api/v1/incidents/{id}", "404"),
        ("unmatched", "404"),
    }


# --- the check itself fails on a leak ---------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("http.request.header.authorization", f"Bearer {WEBHOOK_TOKEN}"),
        ("gen_ai.prompt", "You are ConsoleTrack's triage agent. The alert: ..."),
        ("client.address", CLIENT_IP),
    ],
    ids=["header", "prompt", "ip"],
)
def test_a_header_prompt_or_ip_on_a_span_fails_the_check(
    recorded: Recorded, key: str, value: str
) -> None:
    with tracer.start_as_current_span("triage.run") as span:
        span.set_attribute(key, value)
    assert span_problems(ours(recorded.raw.get_finished_spans()))
    # And the exporter drops the key regardless.
    (exported,) = recorded.exported.get_finished_spans()
    assert key not in (exported.attributes or {})


def test_a_private_value_in_an_allowlisted_key_fails_the_check(recorded: Recorded) -> None:
    with tracer.start_as_current_span("alert.receive", attributes={"alert.name": CLIENT_IP}):
        pass
    assert span_problems(recorded.exported.get_finished_spans()) == [
        "alert.receive: alert.name holds a IP address"
    ]


def test_exported_exceptions_keep_only_their_type(recorded: Recorded) -> None:
    with pytest.raises(RuntimeError), tracer.start_as_current_span("triage.run"):
        raise RuntimeError(f"connect to {CLIENT_IP} failed with {ACCESS_KEY}")
    (span,) = recorded.exported.get_finished_spans()
    (event,) = span.events
    assert set(event.attributes or {}) <= {"exception.type", "exception.escaped"}
    assert event.attributes["exception.type"] == "RuntimeError"
    assert span.status.description is None
    assert span_problems([span]) == []
