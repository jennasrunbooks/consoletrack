"""Incident list and detail, and an admin's decision on the proposed remediation."""

from __future__ import annotations

from datetime import datetime

from app.db.models import IncidentRow, RemediationRow
from app.db.repositories import IncidentFilters, UnitOfWork
from app.enums import RemediationMode, RemediationStatus, SubjectType
from app.schemas import Analysis, Incident, IncidentPage
from app.services import incident_state, remediation_state
from app.services.errors import (
    NoAnalysis,
    NoProposedRemediation,
    NotFound,
    UnsupportedRemediation,
)
from app.services.incident_state import IncidentEvent
from app.services.remediation_apply import APPLY_KINDS
from app.services.remediation_state import RemediationEvent
from app.services.views import analysis_detail, incident_detail, incident_summaries


def list_incidents(
    uow: UnitOfWork, filters: IncidentFilters, *, page: int, page_size: int
) -> IncidentPage:
    rows, total = uow.incidents.list(filters, offset=(page - 1) * page_size, limit=page_size)
    return IncidentPage(
        items=incident_summaries(uow, rows), total=total, page=page, page_size=page_size
    )


def get_incident(uow: UnitOfWork, incident_id: int) -> Incident:
    row = uow.incidents.get(incident_id)
    if row is None:
        raise NotFound("incident", incident_id)
    return incident_detail(uow, row)


def approve_remediation(
    uow: UnitOfWork, incident_id: int, *, actor: str, note: str | None, now: datetime
) -> Incident:
    """An admin approves the proposal: `awaiting_review` → `remediating`, and the backend's apply
    loop runs it through the same validator, dry run, patch, and verify as an auto fix."""
    incident, row = _proposed(uow, incident_id)
    if row.kind not in APPLY_KINDS:
        raise UnsupportedRemediation(
            f"approving a {row.kind.value} remediation is not available yet; only "
            + ", ".join(sorted(APPLY_KINDS))
            + " can be applied"
        )
    incident_state.apply(incident, IncidentEvent.remediation_approved, now=now)
    remediation_state.apply(row, RemediationEvent.approved, now=now, actor=actor)
    row.mode = RemediationMode.manual
    note = note.strip() if note else ""
    _approval_step(row, now, f"approved by {actor}" + (f": {note}" if note else ""))
    uow.commit()
    return incident_detail(uow, incident)


def reject_remediation(
    uow: UnitOfWork, incident_id: int, *, actor: str, reason: str, now: datetime
) -> Incident:
    """An admin rejects the proposal and closes the incident with the reason."""
    incident, row = _proposed(uow, incident_id)
    incident_state.apply(
        incident, IncidentEvent.remediation_rejected, now=now, actor=actor, reason=reason
    )
    remediation_state.apply(row, RemediationEvent.rejected, now=now, actor=actor)
    row.mode = row.mode or RemediationMode.manual
    _approval_step(row, now, f"rejected by {actor}: {reason.strip()}")
    uow.commit()
    return incident_detail(uow, incident)


def save_postmortem(uow: UnitOfWork, incident_id: int, *, postmortem_md: str) -> Analysis:
    """Replace the postmortem on the incident's latest analysis (the agent's draft, or an earlier
    edit). An incident without an analysis has nowhere to keep one yet."""
    incident = uow.incidents.get(incident_id, for_update=True)
    if incident is None:
        raise NotFound("incident", incident_id)
    row = uow.analyses.latest_for(SubjectType.incident, incident_id)
    if row is None:
        raise NoAnalysis(incident_id)
    row.postmortem_md = postmortem_md
    uow.commit()
    result = analysis_detail(uow, row)
    if result is None:  # its agent run is missing, which the foreign key rules out in Postgres
        raise NoAnalysis(incident_id)
    return result


def _proposed(uow: UnitOfWork, incident_id: int) -> tuple[IncidentRow, RemediationRow]:
    incident = uow.incidents.get(incident_id, for_update=True)
    if incident is None:
        raise NotFound("incident", incident_id)
    row = uow.remediations.latest_for(SubjectType.incident, incident_id, for_update=True)
    if row is None or row.status is not RemediationStatus.proposed:
        raise NoProposedRemediation(incident_id)
    return incident, row


def _approval_step(row: RemediationRow, now: datetime, detail: str) -> None:
    entry = {"step": row.status.value, "at": now.isoformat(), "ok": True, "detail": detail}
    row.steps = [*(row.steps or []), entry]
