"""The incident triage queue and the record of each triage run.

The queue is a column, not a broker: `incidents.triage_requested_at`. A firing alert sets it
when it opens an incident, `POST /incidents/{id}/triage` sets it for a re-run, and the worker
claims the oldest due incident with `FOR UPDATE SKIP LOCKED`. Claiming pushes the time forward by
a lease instead of clearing it, so a worker that dies mid-run leaves an incident another worker
picks up once the lease expires. Recording the result clears it.

Status changes go through `incident_state` only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.agents.schema import SubmitAnalysis
from app.db.models import AnalysisRow, IncidentChangeRow, IncidentRow, RemediationRow
from app.db.repositories import UnitOfWork
from app.enums import Confidence, IncidentStatus, RemediationKind, RemediationStatus, SubjectType
from app.schemas import Incident
from app.services import incident_state
from app.services.errors import InvalidModel, NotFound
from app.services.incident_state import IncidentEvent
from app.services.views import incident_detail
from app.settings import Settings


@dataclass(frozen=True)
class TriageClaim:
    """What the worker needs from a claimed incident, read while it was locked."""

    incident_id: int
    alertname: str
    namespace: str | None
    workload: str | None
    labels: dict[str, str]
    annotations: dict[str, str]
    opened_at: datetime
    # The re-run's model override; None = TRIAGE_MODEL.
    model: str | None
    # `incident.open`'s span, which the triage run continues (app/telemetry.py).
    traceparent: str | None = None


def request_triage(row: IncidentRow, *, now: datetime, model: str | None = None) -> None:
    row.triage_requested_at = now
    row.triage_model = model


def rerun_triage(
    uow: UnitOfWork, incident_id: int, *, model: str | None, settings: Settings, now: datetime
) -> Incident:
    """`POST /incidents/{id}/triage`: back to `triaging`, queued for the worker."""
    allowed = {settings.triage_model, settings.triage_model_escalation}
    if model is not None and model not in allowed:
        raise InvalidModel(f"model must be one of: {', '.join(sorted(allowed))}")
    row = uow.incidents.get(incident_id, for_update=True)
    if row is None:
        raise NotFound("incident", incident_id)
    incident_state.apply(row, IncidentEvent.triage_started, now=now)
    request_triage(row, now=now, model=None if model == settings.triage_model else model)
    uow.commit()
    return incident_detail(uow, row)


def claim_next(uow: UnitOfWork, *, now: datetime, lease: timedelta) -> TriageClaim | None:
    """Claim the oldest due incident and move it to `triaging`; None when nothing is due."""
    row = uow.incidents.next_for_triage(now)
    if row is None:
        return None
    if row.status is IncidentStatus.open:
        incident_state.apply(row, IncidentEvent.triage_started, now=now)
    elif row.status is not IncidentStatus.triaging:
        # A request left behind on an incident that moved on (e.g. remediating): drop it.
        row.triage_requested_at = None
        row.triage_model = None
        uow.commit()
        return None
    row.triage_requested_at = now + lease
    uow.commit()
    return TriageClaim(
        incident_id=row.id,
        alertname=row.alertname,
        namespace=row.namespace,
        workload=row.workload,
        labels=dict(row.labels),
        annotations=dict(row.annotations),
        opened_at=row.opened_at,
        model=row.triage_model,
        traceparent=row.traceparent,
    )


def record_triage(
    uow: UnitOfWork,
    incident_id: int,
    *,
    run_id: int,
    analysis: SubmitAnalysis | None,
    stopped: str,
    now: datetime,
) -> RemediationRow | None:
    """Store a finished run's result and move the incident to `awaiting_review`.

    With an analysis: the analysis row, links to the changes it names that exist, and the
    proposal as a `proposed` remediation (returned, for the policy gate). Without one (a limit,
    a failure): an analysis row that says why the run stopped, so the incident page shows it.
    An incident resolved meanwhile keeps the record but doesn't change state.
    """
    row = uow.incidents.get(incident_id, for_update=True)
    if row is None:
        raise NotFound("incident", incident_id)
    row.triage_requested_at = None
    row.triage_model = None
    remediation = None
    if analysis is not None:
        uow.analyses.add(_analysis_row(incident_id, run_id, analysis))
        _link_changes(uow, incident_id, analysis)
        if analysis.proposed_remediation is not None:
            proposal = analysis.proposed_remediation
            remediation = uow.remediations.add(
                RemediationRow(
                    subject_type=SubjectType.incident,
                    subject_id=incident_id,
                    kind=RemediationKind(proposal.kind),
                    payload_json=proposal.model_dump(mode="json"),
                    status=RemediationStatus.proposed,
                )
            )
        event = IncidentEvent.analysis_written
    else:
        uow.analyses.add(
            AnalysisRow(
                subject_type=SubjectType.incident,
                subject_id=incident_id,
                agent_run_id=run_id,
                summary=(
                    f"Triage stopped before the agent submitted an analysis: {stopped}. "
                    "The tool calls it made are on the Evidence tab; triage by hand or re-run."
                ),
                confidence=Confidence.low,
                flags=[],
            )
        )
        event = IncidentEvent.triage_incomplete
    if row.status is IncidentStatus.triaging:
        incident_state.apply(row, event, now=now)
    uow.commit()
    return remediation


def record_superseded_analysis(
    uow: UnitOfWork, incident_id: int, *, run_id: int, analysis: SubmitAnalysis
) -> None:
    """Keep a run's analysis that an escalated run replaced, for the record and the OOM eval.

    Only the analysis row: no links, no remediation, no state change. Stored before the winning
    run's analysis, so that one stays the incident's latest.
    """
    uow.analyses.add(_analysis_row(incident_id, run_id, analysis))
    uow.commit()


def _analysis_row(incident_id: int, run_id: int, analysis: SubmitAnalysis) -> AnalysisRow:
    return AnalysisRow(
        subject_type=SubjectType.incident,
        subject_id=incident_id,
        agent_run_id=run_id,
        summary=analysis.summary,
        root_cause=analysis.root_cause,
        risk=analysis.risk,
        risk_reason=analysis.risk_reason,
        confidence=analysis.confidence,
        flags=[flag.value for flag in analysis.flags],
        postmortem_md=analysis.postmortem_md,
    )


def _link_changes(uow: UnitOfWork, incident_id: int, analysis: SubmitAnalysis) -> None:
    existing = uow.links.changes_for_incidents([incident_id]).get(incident_id, [])
    linked = {change.id for change, _ in existing}
    for change_id in dict.fromkeys(analysis.linked_change_ids):
        # Only changes that exist: a made-up ID is dropped, not stored.
        if change_id in linked or uow.changes.get(change_id) is None:
            continue
        uow.links.add(
            IncidentChangeRow(
                incident_id=incident_id, change_id=change_id, confidence=analysis.confidence
            )
        )
