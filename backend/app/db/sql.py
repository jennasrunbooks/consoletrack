"""SQLAlchemy implementations of the repositories in `app/db/repositories.py`.

This module and `app/db/session.py` are the only code that touches a `Session`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any, Self

from sqlalchemy import ColumnElement, Select, and_, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import (
    AgentRunRow,
    AnalysisRow,
    ChangeRow,
    EvidenceRow,
    IncidentChangeRow,
    IncidentRow,
    IntakeReportRow,
    RemediationRow,
)
from app.db.repositories import ChangeCountsData, ChangeFilters, IncidentFilters, QueueReport
from app.enums import (
    ACTIVE_INCIDENT_STATUSES,
    CLOSED_CHANGE_STATUSES,
    ChangeStatus,
    Confidence,
    RemediationKind,
    RemediationMode,
    RemediationStatus,
    Risk,
    SubjectType,
)


def _contains(column: Any, text: str) -> ColumnElement[bool]:
    """Case-insensitive substring match with LIKE wildcards in `text` escaped."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")


def _change_conditions(filters: ChangeFilters) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if filters.statuses:
        conditions.append(ChangeRow.status.in_(filters.statuses))
    if filters.risks:
        conditions.append(ChangeRow.risk.in_(filters.risks))
    if filters.vias:
        conditions.append(ChangeRow.via.in_(filters.vias))
    if filters.principal:
        conditions.append(
            or_(
                _contains(ChangeRow.principal_arn, filters.principal),
                _contains(ChangeRow.session_name, filters.principal),
            )
        )
    if filters.service:
        conditions.append(ChangeRow.service == filters.service)
    if filters.since:
        conditions.append(ChangeRow.event_time >= filters.since)
    if filters.until:
        conditions.append(ChangeRow.event_time < filters.until)
    if filters.q:
        conditions.append(
            or_(
                *(
                    _contains(column, filters.q)
                    for column in (
                        ChangeRow.title,
                        ChangeRow.resource_name,
                        ChangeRow.resource_arn,
                        ChangeRow.principal_arn,
                        ChangeRow.session_name,
                        ChangeRow.event_name,
                    )
                )
            )
        )
    return conditions


def _page[T](session: Session, query: Select[tuple[T]], offset: int, limit: int) -> list[T]:
    return list(session.scalars(query.offset(offset).limit(limit)))


def _total(session: Session, query: Select[Any]) -> int:
    return session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0


class SqlChangeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, change_id: int, *, for_update: bool = False) -> ChangeRow | None:
        return self.session.get(ChangeRow, change_id, with_for_update=for_update)

    def get_by_event_id(self, cloudtrail_event_id: str) -> ChangeRow | None:
        return self.session.scalar(
            select(ChangeRow).where(ChangeRow.cloudtrail_event_id == cloudtrail_event_id)
        )

    def add(self, row: ChangeRow) -> ChangeRow:
        self.session.add(row)
        self.session.flush()
        return row

    def list(
        self, filters: ChangeFilters, *, offset: int, limit: int
    ) -> tuple[list[ChangeRow], int]:
        query = (
            select(ChangeRow)
            .where(*_change_conditions(filters))
            .order_by(ChangeRow.event_time.desc(), ChangeRow.id.desc())
        )
        return _page(self.session, query, offset, limit), _total(self.session, query)

    def counts(self, filters: ChangeFilters, *, closed_since: datetime) -> ChangeCountsData:
        conditions = _change_conditions(filters)
        by_status = {
            status: count
            for status, count in self.session.execute(
                select(ChangeRow.status, func.count()).where(*conditions).group_by(ChangeRow.status)
            )
        }
        closed = ChangeRow.status.in_(CLOSED_CHANGE_STATUSES)
        linked = exists().where(IncidentChangeRow.change_id == ChangeRow.id)
        high_risk_open, linked_to_incident, closed_recent = self.session.execute(
            select(
                func.count().filter(and_(ChangeRow.risk == Risk.high, ~closed)),
                func.count().filter(linked),
                func.count().filter(and_(closed, ChangeRow.decided_at >= closed_since)),
            ).where(*conditions)
        ).one()
        return ChangeCountsData(
            by_status=by_status,
            high_risk_open=high_risk_open,
            linked_to_incident=linked_to_incident,
            closed_since=closed_recent,
        )

    def last_detected_at(self) -> datetime | None:
        return self.session.scalar(select(func.max(ChangeRow.detected_at)))

    def next_for_analysis(self, *, lease_start: datetime, max_runs: int) -> ChangeRow | None:
        runs = select(func.count(AgentRunRow.id)).where(
            AgentRunRow.subject_type == SubjectType.change, AgentRunRow.subject_id == ChangeRow.id
        )
        return self.session.scalar(
            select(ChangeRow)
            .where(
                ChangeRow.status == ChangeStatus.detected,
                ~exists().where(
                    AnalysisRow.subject_type == SubjectType.change,
                    AnalysisRow.subject_id == ChangeRow.id,
                ),
                ~exists().where(
                    AgentRunRow.subject_type == SubjectType.change,
                    AgentRunRow.subject_id == ChangeRow.id,
                    AgentRunRow.started_at >= lease_start,
                ),
                runs.scalar_subquery() < max_runs,
            )
            .order_by(ChangeRow.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )


class SqlIncidentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, incident_id: int, *, for_update: bool = False) -> IncidentRow | None:
        return self.session.get(IncidentRow, incident_id, with_for_update=for_update)

    def find_active_by_fingerprint(self, fingerprint: str) -> IncidentRow | None:
        return self.session.scalar(
            select(IncidentRow).where(
                IncidentRow.fingerprint == fingerprint,
                IncidentRow.status.in_(ACTIVE_INCIDENT_STATUSES),
            )
        )

    def add(self, row: IncidentRow) -> IncidentRow:
        self.session.add(row)
        self.session.flush()
        return row

    def list(
        self, filters: IncidentFilters, *, offset: int, limit: int
    ) -> tuple[list[IncidentRow], int]:
        conditions: list[ColumnElement[bool]] = []
        if filters.statuses:
            conditions.append(IncidentRow.status.in_(filters.statuses))
        if filters.severity:
            conditions.append(IncidentRow.severity == filters.severity)
        if filters.namespace:
            conditions.append(IncidentRow.namespace == filters.namespace)
        query = (
            select(IncidentRow)
            .where(*conditions)
            .order_by(IncidentRow.opened_at.desc(), IncidentRow.id.desc())
        )
        return _page(self.session, query, offset, limit), _total(self.session, query)

    def next_for_triage(self, now: datetime) -> IncidentRow | None:
        return self.session.scalar(
            select(IncidentRow)
            .where(
                IncidentRow.triage_requested_at <= now,
                IncidentRow.status.in_(ACTIVE_INCIDENT_STATUSES),
            )
            .order_by(IncidentRow.triage_requested_at, IncidentRow.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )


class SqlIncidentChangeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, row: IncidentChangeRow) -> IncidentChangeRow:
        self.session.add(row)
        self.session.flush()
        return row

    def incidents_for_changes(
        self, change_ids: Sequence[int]
    ) -> dict[int, list[tuple[IncidentRow, Confidence]]]:
        result: dict[int, list[tuple[IncidentRow, Confidence]]] = defaultdict(list)
        if not change_ids:
            return result
        rows = self.session.execute(
            select(IncidentChangeRow.change_id, IncidentRow, IncidentChangeRow.confidence)
            .join(IncidentRow, IncidentRow.id == IncidentChangeRow.incident_id)
            .where(IncidentChangeRow.change_id.in_(change_ids))
            .order_by(IncidentRow.opened_at.desc(), IncidentRow.id.desc())
        )
        for change_id, incident, confidence in rows:
            result[change_id].append((incident, confidence))
        return result

    def changes_for_incidents(
        self, incident_ids: Sequence[int]
    ) -> dict[int, list[tuple[ChangeRow, Confidence]]]:
        result: dict[int, list[tuple[ChangeRow, Confidence]]] = defaultdict(list)
        if not incident_ids:
            return result
        rows = self.session.execute(
            select(IncidentChangeRow.incident_id, ChangeRow, IncidentChangeRow.confidence)
            .join(ChangeRow, ChangeRow.id == IncidentChangeRow.change_id)
            .where(IncidentChangeRow.incident_id.in_(incident_ids))
            .order_by(ChangeRow.event_time.desc(), ChangeRow.id.desc())
        )
        for incident_id, change, confidence in rows:
            result[incident_id].append((change, confidence))
        return result


class SqlAgentRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, row: AgentRunRow) -> AgentRunRow:
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, run_id: int) -> AgentRunRow | None:
        return self.session.get(AgentRunRow, run_id)

    def any_escalated(self, subject_type: SubjectType, subject_id: int) -> bool:
        return bool(
            self.session.scalar(
                select(
                    exists().where(
                        AgentRunRow.subject_type == subject_type,
                        AgentRunRow.subject_id == subject_id,
                        AgentRunRow.escalated.is_(True),
                    )
                )
            )
        )


class SqlEvidenceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, row: EvidenceRow) -> EvidenceRow:
        self.session.add(row)
        self.session.flush()
        return row

    def list_for_subject(self, subject_type: SubjectType, subject_id: int) -> list[EvidenceRow]:
        return list(
            self.session.scalars(
                select(EvidenceRow)
                .join(AgentRunRow, AgentRunRow.id == EvidenceRow.agent_run_id)
                .where(
                    AgentRunRow.subject_type == subject_type,
                    AgentRunRow.subject_id == subject_id,
                )
                .order_by(AgentRunRow.started_at, AgentRunRow.id, EvidenceRow.seq)
            )
        )

    def count_for_run(self, run_id: int) -> int:
        return (
            self.session.scalar(select(func.count()).where(EvidenceRow.agent_run_id == run_id)) or 0
        )


class SqlAnalysisRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, row: AnalysisRow) -> AnalysisRow:
        self.session.add(row)
        self.session.flush()
        return row

    def latest_for(self, subject_type: SubjectType, subject_id: int) -> AnalysisRow | None:
        return self.session.scalar(
            select(AnalysisRow)
            .where(AnalysisRow.subject_type == subject_type, AnalysisRow.subject_id == subject_id)
            .order_by(AnalysisRow.id.desc())
            .limit(1)
        )


class SqlRemediationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, row: RemediationRow) -> RemediationRow:
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, remediation_id: int, *, for_update: bool = False) -> RemediationRow | None:
        return self.session.get(RemediationRow, remediation_id, with_for_update=for_update)

    def latest_for(
        self, subject_type: SubjectType, subject_id: int, *, for_update: bool = False
    ) -> RemediationRow | None:
        query = (
            select(RemediationRow)
            .where(
                RemediationRow.subject_type == subject_type,
                RemediationRow.subject_id == subject_id,
            )
            .order_by(RemediationRow.id.desc())
            .limit(1)
        )
        return self.session.scalar(query.with_for_update() if for_update else query)

    def auto_for_workload(self, namespace: str, workload: str) -> list[RemediationRow]:
        return list(
            self.session.scalars(
                select(RemediationRow)
                .where(
                    RemediationRow.namespace == namespace,
                    RemediationRow.workload == workload,
                    RemediationRow.mode == RemediationMode.auto,
                )
                .order_by(RemediationRow.id)
            )
        )

    def next_to_apply(
        self, now: datetime, kinds: Collection[RemediationKind]
    ) -> RemediationRow | None:
        return self.session.scalar(
            select(RemediationRow)
            .where(
                RemediationRow.kind.in_(list(kinds)),
                or_(
                    RemediationRow.status == RemediationStatus.approved,
                    and_(
                        RemediationRow.status == RemediationStatus.running,
                        or_(
                            RemediationRow.lease_until.is_(None),
                            RemediationRow.lease_until < now,
                        ),
                    ),
                ),
            )
            .order_by(RemediationRow.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )


class SqlIntakeReportRepository:
    """Upserts on the single row, so neither writer needs it to exist first."""

    ROW_ID = 1

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self) -> IntakeReportRow | None:
        return self.session.get(IntakeReportRow, self.ROW_ID)

    def save_report(self, report: QueueReport, *, reported_at: datetime) -> None:
        values = {**vars(report), "reported_at": reported_at}
        self.session.execute(
            insert(IntakeReportRow)
            .values(id=self.ROW_ID, **values)
            .on_conflict_do_update(index_elements=[IntakeReportRow.id], set_=values)
        )

    def save_canary(self, event_time: datetime) -> None:
        stmt = insert(IntakeReportRow).values(id=self.ROW_ID, last_canary_at=event_time)
        self.session.execute(
            stmt.on_conflict_do_update(
                index_elements=[IntakeReportRow.id],
                set_={
                    "last_canary_at": func.greatest(
                        IntakeReportRow.last_canary_at, stmt.excluded.last_canary_at
                    )
                },
            )
        )


class SqlUnitOfWork:
    """Repositories over one session. Leaving the block without `commit()` rolls back."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.changes = SqlChangeRepository(session)
        self.incidents = SqlIncidentRepository(session)
        self.links = SqlIncidentChangeRepository(session)
        self.agent_runs = SqlAgentRunRepository(session)
        self.evidence = SqlEvidenceRepository(session)
        self.analyses = SqlAnalysisRepository(session)
        self.remediations = SqlRemediationRepository(session)
        self.intake_reports = SqlIntakeReportRepository(session)

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.session.rollback()
        self.session.close()
