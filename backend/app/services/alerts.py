"""Alertmanager webhook → incidents.

Each alert in a delivery is handled on its own status, because one grouped delivery can carry
firing and resolved alerts together. A firing alert opens an incident unless one with the same
fingerprint is still unresolved; a resolved alert resolves that incident.

Triage never runs inside the webhook request: a new incident is queued
(`incidents.triage_requested_at`) and the worker picks it up (`app/services/triage.py`).
"""

from __future__ import annotations

from datetime import datetime

from app.db.models import IncidentRow
from app.db.repositories import UnitOfWork
from app.schemas import AlertmanagerAlert, AlertmanagerWebhook, AlertWebhookResult
from app.services import incident_state
from app.services.incident_state import IncidentEvent

# Labels that name the affected workload, most specific first. kube-prometheus-stack rules
# carry `deployment`/`statefulset`/`daemonset`; ConsoleTrack's own rules set `workload`.
WORKLOAD_LABELS = ("workload", "deployment", "statefulset", "daemonset", "job_name", "pod")
UNKNOWN_ALERTNAME = "unknown"


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


def process_alertmanager(
    uow: UnitOfWork, payload: AlertmanagerWebhook, *, now: datetime
) -> AlertWebhookResult:
    opened: list[int] = []
    resolved: list[int] = []
    ignored = 0
    for alert in payload.alerts:
        active = uow.incidents.find_active_by_fingerprint(alert.fingerprint)
        if alert.status == "firing":
            if active is not None:
                ignored += 1  # re-notification of an open episode
                continue
            opened.append(uow.incidents.add(new_incident(alert, now=now)).id)
        else:
            if active is None:
                ignored += 1  # already resolved or closed, or never seen
                continue
            incident_state.apply(active, IncidentEvent.alert_resolved, now=now)
            # Alertmanager's endsAt is when the alert stopped firing; fall back to now.
            if alert.endsAt is not None and alert.endsAt >= active.opened_at:
                active.resolved_at = min(alert.endsAt, now)
            resolved.append(active.id)
    uow.commit()
    return AlertWebhookResult(opened=opened, resolved=resolved, ignored=ignored)
