"""The agent's tool calls for a change or an incident."""

from __future__ import annotations

from app.db.repositories import UnitOfWork
from app.enums import SubjectType
from app.schemas import Evidence, EvidenceList
from app.services.errors import NotFound


def list_evidence(uow: UnitOfWork, subject_type: SubjectType, subject_id: int) -> EvidenceList:
    repo = uow.changes if subject_type is SubjectType.change else uow.incidents
    if repo.get(subject_id) is None:
        raise NotFound(subject_type.value, subject_id)
    return EvidenceList(
        items=[
            Evidence(
                id=row.id,
                agent_run_id=row.agent_run_id,
                seq=row.seq,
                tool_name=row.tool_name,
                args=row.args_json,
                result_excerpt=row.result_excerpt,
                created_at=row.created_at,
            )
            for row in uow.evidence.list_for_subject(subject_type, subject_id)
        ]
    )
