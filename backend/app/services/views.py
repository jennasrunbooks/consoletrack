"""Build API views (`app/schemas.py`) from rows, fetching related rows through the unit of work."""

from __future__ import annotations

from app.db.models import AnalysisRow, ChangeRow, IncidentRow, RemediationRow
from app.db.repositories import UnitOfWork
from app.enums import SubjectType
from app.schemas import (
    AgentRun,
    Analysis,
    Change,
    ChangeRef,
    ChangeSummary,
    Drift,
    Incident,
    IncidentRef,
    IncidentSummary,
    LinkedChange,
    LinkedIncident,
    Remediation,
    RemediationStep,
)


def change_title(row: ChangeRow) -> str:
    """The agent's title, or one built from the event and resource until the agent has run."""
    return row.title or f"{row.event_name} on {row.resource_name or row.resource_arn}"


def incident_ref(row: IncidentRow) -> IncidentRef:
    return IncidentRef(
        id=row.id,
        alertname=row.alertname,
        namespace=row.namespace,
        workload=row.workload,
        status=row.status,
    )


def change_ref(row: ChangeRow) -> ChangeRef:
    return ChangeRef(
        id=row.id,
        title=change_title(row),
        event_name=row.event_name,
        risk=row.risk,
        status=row.status,
    )


def change_summaries(uow: UnitOfWork, rows: list[ChangeRow]) -> list[ChangeSummary]:
    links = uow.links.incidents_for_changes([row.id for row in rows])
    return [
        ChangeSummary(
            id=row.id,
            title=change_title(row),
            event_name=row.event_name,
            service=row.service,
            event_time=row.event_time,
            detected_at=row.detected_at,
            resource_arn=row.resource_arn,
            resource_name=row.resource_name,
            tf_address=row.tf_address,
            principal_arn=row.principal_arn,
            session_name=row.session_name,
            via=row.via,
            risk=row.risk,
            status=row.status,
            linked_incidents=[incident_ref(incident) for incident, _ in links.get(row.id, [])],
        )
        for row in rows
    ]


def change_detail(uow: UnitOfWork, row: ChangeRow) -> Change:
    links = uow.links.incidents_for_changes([row.id]).get(row.id, [])
    return Change(
        id=row.id,
        title=change_title(row),
        event_name=row.event_name,
        service=row.service,
        event_time=row.event_time,
        detected_at=row.detected_at,
        resource_arn=row.resource_arn,
        resource_name=row.resource_name,
        tf_address=row.tf_address,
        principal_arn=row.principal_arn,
        session_name=row.session_name,
        via=row.via,
        risk=row.risk,
        status=row.status,
        cloudtrail_event_id=row.cloudtrail_event_id,
        region=row.region,
        source_ip=row.source_ip,
        user_agent=row.user_agent,
        request_parameters=row.request_parameters,
        decision=row.decision,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        reason=row.reason,
        analysis=_analysis(uow, uow.analyses.latest_for(SubjectType.change, row.id)),
        drift=Drift.model_validate(row.drift) if row.drift else None,
        remediation=_remediation(uow.remediations.latest_for(SubjectType.change, row.id)),
        linked_incidents=[
            LinkedIncident(incident=incident_ref(incident), confidence=confidence)
            for incident, confidence in links
        ],
    )


def incident_summaries(uow: UnitOfWork, rows: list[IncidentRow]) -> list[IncidentSummary]:
    links = uow.links.changes_for_incidents([row.id for row in rows])
    return [
        IncidentSummary(
            id=row.id,
            alertname=row.alertname,
            severity=row.severity,
            namespace=row.namespace,
            workload=row.workload,
            status=row.status,
            opened_at=row.opened_at,
            resolved_at=row.resolved_at,
            linked_changes=[change_ref(change) for change, _ in links.get(row.id, [])],
        )
        for row in rows
    ]


def incident_detail(uow: UnitOfWork, row: IncidentRow) -> Incident:
    links = uow.links.changes_for_incidents([row.id]).get(row.id, [])
    time_to_resolve = None
    if row.resolved_at is not None:
        time_to_resolve = max(0, int((row.resolved_at - row.opened_at).total_seconds()))
    return Incident(
        id=row.id,
        alertname=row.alertname,
        severity=row.severity,
        namespace=row.namespace,
        workload=row.workload,
        status=row.status,
        opened_at=row.opened_at,
        resolved_at=row.resolved_at,
        fingerprint=row.fingerprint,
        labels=row.labels,
        annotations=row.annotations,
        time_to_resolve_seconds=time_to_resolve,
        analysis=_analysis(uow, uow.analyses.latest_for(SubjectType.incident, row.id)),
        remediation=_remediation(uow.remediations.latest_for(SubjectType.incident, row.id)),
        linked_changes=[
            LinkedChange(change=change_ref(change), confidence=confidence)
            for change, confidence in links
        ],
    )


def _analysis(uow: UnitOfWork, row: AnalysisRow | None) -> Analysis | None:
    if row is None:
        return None
    run = uow.agent_runs.get(row.agent_run_id)
    if run is None:  # the FK makes this unreachable in Postgres
        return None
    return Analysis(
        id=row.id,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        summary=row.summary,
        risk=row.risk,
        risk_reason=row.risk_reason,
        root_cause=row.root_cause,
        confidence=row.confidence,
        postmortem_md=row.postmortem_md,
        agent_run=AgentRun(
            id=run.id,
            model=run.model,
            started_at=run.started_at,
            finished_at=run.finished_at,
            tool_calls=uow.evidence.count_for_run(run.id),
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
            outcome=run.outcome,
        ),
    )


def _remediation(row: RemediationRow | None) -> Remediation | None:
    if row is None:
        return None
    return Remediation(
        id=row.id,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        kind=row.kind,
        status=row.status,
        payload=row.payload_json,
        decided_by=row.decided_by,
        external_url=row.external_url,
        created_at=row.created_at,
        applied_at=row.applied_at,
        error=row.error,
        mode=row.mode,
        policy=row.policy,
        gate_reason=row.gate_reason,
        decided_at=row.decided_at,
        before=row.before_json,
        after=row.after_json,
        steps=[RemediationStep.model_validate(step) for step in row.steps or []],
    )
