"""Alertmanager webhook → incidents.

Each alert in a delivery is handled on its own status, because one grouped delivery can carry
firing and resolved alerts together. A firing alert opens an incident unless one with the same
fingerprint is still unresolved; a resolved alert resolves that incident.

Triage never runs inside the webhook request: a new incident is queued
(`incidents.triage_requested_at`) and the worker picks it up (`app/services/triage.py`).

Tracing (docs/observability.md): each alert gets an `alert.receive` span. One that opens an
incident starts a new trace, linked to the HTTP request's, and `incident.open`'s traceparent is
stored on the incident. Any later alert for that incident (the resolved notification, a
re-notification) continues the incident's trace, so `incident.resolve` lands in it too.
"""

from __future__ import annotations

from datetime import datetime

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Link

from app import telemetry
from app.db.models import IncidentRow
from app.db.repositories import UnitOfWork
from app.enums import RemediationMode, RemediationStatus, SubjectType
from app.schemas import AlertmanagerAlert, AlertmanagerWebhook, AlertWebhookResult
from app.services import incident_state
from app.services.incident_state import IncidentEvent
from app.telemetry import tracer

# Labels that name the affected workload, most specific first. kube-prometheus-stack rules
# carry `deployment`/`statefulset`/`daemonset`; ConsoleTrack's own rules set `workload`.
WORKLOAD_LABELS = ("workload", "deployment", "statefulset", "daemonset", "job_name", "pod")
UNKNOWN_ALERTNAME = "unknown"
# A remediation in these states fixed (or is fixing) the incident when it resolves; the apply
# loop's verify step is often still waiting for this very notification.
FIXING = frozenset(
    {RemediationStatus.approved, RemediationStatus.running, RemediationStatus.succeeded}
)


def workload_of(labels: dict[str, str]) -> str | None:
    return next((labels[key] for key in WORKLOAD_LABELS if labels.get(key)), None)


def new_incident(alert: AlertmanagerAlert, *, now: datetime) -> IncidentRow:
    """A new incident in `open`, queued for the triage worker."""
    labels = alert.labels
    return IncidentRow(
        fingerprint=alert.fingerprint,
        alertname=labels.get("alertname") or UNKNOWN_ALERTNAME,
        severity=labels.get("severity"),
        namespace=labels.get("namespace"),
        workload=workload_of(labels),
        labels=dict(labels),
        annotations=dict(alert.annotations),
        opened_at=alert.startsAt,
        status=incident_state.INITIAL,
        triage_requested_at=now,
    )


def resolution_of(uow: UnitOfWork, incident: IncidentRow) -> str:
    """`auto_fixed`, `approved_fix`, or `manual` (no ConsoleTrack fix), for the metrics."""
    row = uow.remediations.latest_for(SubjectType.incident, incident.id)
    if row is None or row.status not in FIXING:
        return "manual"
    return "auto_fixed" if row.mode is RemediationMode.auto else "approved_fix"


def process_alertmanager(
    uow: UnitOfWork, payload: AlertmanagerWebhook, *, now: datetime
) -> AlertWebhookResult:
    opened: list[int] = []
    resolved: list[int] = []
    ignored = 0
    request = trace.get_current_span().get_span_context()
    links = [Link(request)] if request.is_valid else []
    # Recorded once the transaction commits.
    opened_severities: list[str | None] = []
    resolutions: list[tuple[str, float]] = []
    for alert in payload.alerts:
        active = uow.incidents.find_active_by_fingerprint(alert.fingerprint)
        if active is not None:
            parent = telemetry.context_from(active.traceparent)
        else:
            # A new episode is a new trace; an unknown resolved alert stays in the request's.
            parent = Context() if alert.status == "firing" else None
        attributes = {
            "alert.name": alert.labels.get("alertname") or UNKNOWN_ALERTNAME,
            "alert.fingerprint": telemetry.hashed(alert.fingerprint),
            "alert.status": alert.status,
        }
        with tracer.start_as_current_span(
            "alert.receive", context=parent, links=links, attributes=attributes
        ):
            if alert.status == "firing":
                if active is not None:
                    ignored += 1  # re-notification of an open episode
                    continue
                opened.append(_open(uow, alert, now=now))
                opened_severities.append(alert.labels.get("severity"))
            else:
                if active is None:
                    ignored += 1  # already resolved or closed, or never seen
                    continue
                resolutions.append(_resolve(uow, active, alert, now=now))
                resolved.append(active.id)
    uow.commit()
    for severity in opened_severities:
        telemetry.INCIDENTS_OPENED.add(1, {"severity": telemetry.severity_label(severity)})
    for resolution, seconds in resolutions:
        telemetry.INCIDENTS_RESOLVED.add(1, {"resolution": resolution})
        telemetry.TIME_TO_RESOLVE.record(seconds)
    return AlertWebhookResult(opened=opened, resolved=resolved, ignored=ignored)


def _open(uow: UnitOfWork, alert: AlertmanagerAlert, *, now: datetime) -> int:
    with tracer.start_as_current_span("incident.open") as span:
        row = new_incident(alert, now=now)
        row.traceparent = telemetry.current_traceparent()
        row = uow.incidents.add(row)
        span.set_attributes(
            {
                "incident.id": row.id,
                "incident.severity": telemetry.severity_label(row.severity),
                "incident.state": row.status.value,
            }
        )
        return row.id


def _resolve(
    uow: UnitOfWork, active: IncidentRow, alert: AlertmanagerAlert, *, now: datetime
) -> tuple[str, float]:
    with tracer.start_as_current_span("incident.resolve") as span:
        incident_state.apply(active, IncidentEvent.alert_resolved, now=now)
        # Alertmanager's endsAt is when the alert stopped firing; fall back to now.
        if alert.endsAt is not None and alert.endsAt >= active.opened_at:
            active.resolved_at = min(alert.endsAt, now)
        resolved_at = active.resolved_at or now
        seconds = max((resolved_at - active.opened_at).total_seconds(), 0.0)
        resolution = resolution_of(uow, active)
        span.set_attributes(
            {
                "incident.id": active.id,
                "incident.state": active.status.value,
                "incident.resolution": resolution,
                "incident.time_to_resolve_s": seconds,
            }
        )
        return resolution, seconds
