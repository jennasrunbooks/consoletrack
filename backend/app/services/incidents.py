"""Incident list and detail."""

from __future__ import annotations

from app.db.repositories import IncidentFilters, UnitOfWork
from app.schemas import Incident, IncidentPage
from app.services.errors import NotFound
from app.services.views import incident_detail, incident_summaries


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
