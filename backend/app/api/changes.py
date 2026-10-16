"""/api/v1/changes: the change log and its review decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.api.deps import Admin, Now, Uow, Viewer
from app.api.errors import NotImplementedYet, error_responses
from app.db.repositories import ChangeFilters
from app.enums import ChangeStatus, Risk, Via
from app.schemas import (
    AcceptRequest,
    Change,
    ChangePage,
    CodifyRequest,
    RevertRequest,
    RevertResponse,
)
from app.services import changes

router = APIRouter(prefix="/api/v1/changes", tags=["changes"])

ChangeId = Annotated[int, Path(ge=1, json_schema_extra={"format": "int64"})]
Page = Annotated[int, Query(ge=1, description="1-based page number.")]
PageSize = Annotated[int, Query(ge=1, le=100)]
WRITE_ERRORS = error_responses(400, 401, 403, 404, 409, 422)


@router.get(
    "",
    operation_id="listChanges",
    response_model=ChangePage,
    responses=error_responses(400, 401, 422),
)
def list_changes(
    uow: Uow,
    now: Now,
    _: Viewer,
    status: Annotated[list[ChangeStatus] | None, Query()] = None,
    risk: Annotated[list[Risk] | None, Query()] = None,
    via: Annotated[list[Via] | None, Query()] = None,
    principal: Annotated[str | None, Query(max_length=256)] = None,
    service: Annotated[str | None, Query(max_length=64, pattern="^[a-z0-9-]+$")] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    q: Annotated[str | None, Query(max_length=256)] = None,
    page: Page = 1,
    page_size: PageSize = 25,
) -> ChangePage:
    """List changes (the change log), newest first by `event_time`."""
    filters = ChangeFilters(
        statuses=tuple(status or ()),
        risks=tuple(risk or ()),
        vias=tuple(via or ()),
        principal=principal or None,
        service=service or None,
        since=since,
        until=until,
        q=q or None,
    )
    return changes.list_changes(uow, filters, page=page, page_size=page_size, now=now)


@router.get(
    "/{id}",
    operation_id="getChange",
    response_model=Change,
    responses=error_responses(400, 401, 404, 422),
)
def get_change(id: ChangeId, uow: Uow, _: Viewer) -> Change:
    """One change with its analysis, drift, linked incidents, and remediation."""
    return changes.get_change(uow, id)


@router.post(
    "/{id}/revert",
    operation_id="revertChange",
    status_code=202,
    response_model=RevertResponse,
    responses=WRITE_ERRORS,
)
def revert_change(id: ChangeId, _: Admin, body: RevertRequest | None = None) -> RevertResponse:
    """Revert to Terraform through the gated workflow. Requires admin."""
    raise NotImplementedYet("POST /api/v1/changes/{id}/revert", "#14 (gated Revert)")


@router.post(
    "/{id}/accept", operation_id="acceptChange", response_model=Change, responses=WRITE_ERRORS
)
def accept_change(id: ChangeId, body: AcceptRequest, uow: Uow, now: Now, admin: Admin) -> Change:
    """Accept the change with a required note. Requires admin."""
    return changes.accept_change(uow, id, note=body.note, actor=admin.username, now=now)


@router.post(
    "/{id}/codify",
    operation_id="codifyChange",
    status_code=202,
    response_model=Change,
    responses=error_responses(400, 401, 403, 404, 409, 422, 501),
    openapi_extra={"x-phase": 2},
)
def codify_change(id: ChangeId, _: Admin, body: CodifyRequest | None = None) -> Change:
    """Open a pull request that adopts the change in Terraform (Phase 2). Requires admin."""
    raise NotImplementedYet("POST /api/v1/changes/{id}/codify", "Phase 2")
