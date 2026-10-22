"""/api/v1/incidents: alert-driven incidents, triage, and remediation decisions."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.api.deps import Admin, AppSettings, Now, Uow, Viewer
from app.api.errors import error_responses
from app.db.repositories import IncidentFilters
from app.enums import IncidentStatus
from app.schemas import (
    Analysis,
    ApproveRemediationRequest,
    Incident,
    IncidentPage,
    PostmortemRequest,
    RejectRemediationRequest,
    TriageRequest,
)
from app.services import incidents, triage

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])

IncidentId = Annotated[int, Path(ge=1, json_schema_extra={"format": "int64"})]
WRITE_ERRORS = error_responses(400, 401, 403, 404, 409, 422)


@router.get(
    "",
    operation_id="listIncidents",
    response_model=IncidentPage,
    responses=error_responses(400, 401, 422),
)
def list_incidents(
    uow: Uow,
    _: Viewer,
    status: Annotated[list[IncidentStatus] | None, Query()] = None,
    severity: Annotated[str | None, Query(max_length=64)] = None,
    namespace: Annotated[str | None, Query(max_length=63)] = None,
    page: Annotated[int, Query(ge=1, description="1-based page number.")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> IncidentPage:
    """List incidents, newest first by `opened_at`."""
    filters = IncidentFilters(
        statuses=tuple(status or ()), severity=severity or None, namespace=namespace or None
    )
    return incidents.list_incidents(uow, filters, page=page, page_size=page_size)


@router.get(
    "/{id}",
    operation_id="getIncident",
    response_model=Incident,
    responses=error_responses(400, 401, 404, 422),
)
def get_incident(id: IncidentId, uow: Uow, _: Viewer) -> Incident:
    """One incident with its analysis, linked changes, and remediation."""
    return incidents.get_incident(uow, id)


@router.post(
    "/{id}/triage",
    operation_id="triageIncident",
    status_code=202,
    response_model=Incident,
    responses=WRITE_ERRORS,
)
def triage_incident(
    id: IncidentId,
    uow: Uow,
    now: Now,
    settings: AppSettings,
    _: Admin,
    body: TriageRequest | None = None,
) -> Incident:
    """Re-run triage, optionally with another model. Requires admin."""
    model = body.model if body is not None else None
    return triage.rerun_triage(uow, id, model=model, settings=settings, now=now)


@router.post(
    "/{id}/remediation/approve",
    operation_id="approveIncidentRemediation",
    response_model=Incident,
    responses=WRITE_ERRORS,
)
def approve_remediation(
    id: IncidentId, uow: Uow, now: Now, user: Admin, body: ApproveRemediationRequest | None = None
) -> Incident:
    """Approve the proposed remediation. Requires admin."""
    note = body.note if body is not None else None
    return incidents.approve_remediation(uow, id, actor=user.username, note=note, now=now)


@router.post(
    "/{id}/remediation/reject",
    operation_id="rejectIncidentRemediation",
    response_model=Incident,
    responses=WRITE_ERRORS,
)
def reject_remediation(
    id: IncidentId, body: RejectRemediationRequest, uow: Uow, now: Now, user: Admin
) -> Incident:
    """Reject the proposed remediation and close the incident. Requires admin."""
    return incidents.reject_remediation(uow, id, actor=user.username, reason=body.reason, now=now)


@router.put(
    "/{id}/postmortem",
    operation_id="savePostmortem",
    response_model=Analysis,
    responses=WRITE_ERRORS,
)
def save_postmortem(id: IncidentId, body: PostmortemRequest, uow: Uow, _: Admin) -> Analysis:
    """Save the edited postmortem. Requires admin."""
    return incidents.save_postmortem(uow, id, postmortem_md=body.postmortem_md)
