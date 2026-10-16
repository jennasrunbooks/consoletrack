"""/api/v1/{subject}/{id}/evidence: the agent's tool calls (read only)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path

from app.api.deps import Uow, Viewer
from app.api.errors import error_responses
from app.enums import SubjectPath
from app.schemas import EvidenceList
from app.services import evidence

router = APIRouter(prefix="/api/v1", tags=["evidence"])


@router.get(
    "/{subject}/{id}/evidence",
    operation_id="listEvidence",
    response_model=EvidenceList,
    responses=error_responses(400, 401, 404, 422),
)
def list_evidence(
    subject: SubjectPath,
    id: Annotated[int, Path(ge=1, json_schema_extra={"format": "int64"})],
    uow: Uow,
    _: Viewer,
) -> EvidenceList:
    """Tool calls the agent made for a change or an incident, oldest first."""
    return evidence.list_evidence(uow, subject.subject_type, id)
