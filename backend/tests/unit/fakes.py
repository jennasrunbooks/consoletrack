"""In-memory repositories implementing `app/db/repositories.py` for unit tests.

They hold real (transient) ORM objects in lists, so services run unchanged. The filters mirror
`app/db/sql.py`; `tests/integration/` checks the SQL versions against real Postgres.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Self

from app.db.models import (
    AgentRunRow,
    AnalysisRow,
    ChangeRow,
    EvidenceRow,
    IncidentChangeRow,
    IncidentRow,
    RemediationRow,
)
from app.db.repositories import ChangeCountsData, ChangeFilters, IncidentFilters
from app.enums import (
    ACTIVE_INCIDENT_STATUSES,
    CLOSED_CHANGE_STATUSES,
    Confidence,
    Risk,
    SubjectType,
)

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


class _Table[T]:
    def __init__(self) -> None:
        self.rows: list[T] = []
        self._next_id = 1

    def insert(self, row: T) -> T:
        if getattr(row, "id", None) is None and hasattr(row, "id"):
            row.id = self._next_id  # type: ignore[attr-defined]
        self._next_id += 1
        # Column defaults the database would fill in.
        for name in ("created_at", "updated_at", "detected_at"):
            if hasattr(row, name) and getattr(row, name) is None:
                setattr(row, name, EPOCH)
        for column in row.__table__.columns:  # type: ignore[attr-defined]
            if getattr(row, column.key) is None and column.default is not None:
                default = column.default.arg
                setattr(row, column.key, default({}) if callable(default) else default)
        self.rows.append(row)
        return row


def _contains(value: str | None, text: str) -> bool:
    return value is not None and text.lower() in value.lower()


def _matches(row: ChangeRow, f: ChangeFilters) -> bool:
    principal_ok = not f.principal or (
        _contains(row.principal_arn, f.principal) or _contains(row.session_name, f.principal)
    )
    q_ok = not f.q or any(
        _contains(value, f.q)
        for value in (
            row.title,
            row.resource_name,
            row.resource_arn,
            row.principal_arn,
            row.session_name,
            row.event_name,
        )
    )
    return (
        (not f.statuses or row.status in f.statuses)
        and (not f.risks or row.risk in f.risks)
        and (not f.vias or row.via in f.vias)
        and principal_ok
        and (not f.service or row.service == f.service)
        and (f.since is None or row.event_time >= f.since)
        and (f.until is None or row.event_time < f.until)
        and q_ok
    )


class FakeChangeRepository:
    def __init__(self, store: FakeStore) -> None:
        self.store = store

    def get(self, change_id: int, *, for_update: bool = False) -> ChangeRow | None:
        return next((row for row in self.store.changes.rows if row.id == change_id), None)

    def get_by_event_id(self, cloudtrail_event_id: str) -> ChangeRow | None:
        return next(
            (
                row
                for row in self.store.changes.rows
                if row.cloudtrail_event_id == cloudtrail_event_id
            ),
            None,
        )

    def add(self, row: ChangeRow) -> ChangeRow:
        return self.store.changes.insert(row)

    def list(
        self, filters: ChangeFilters, *, offset: int, limit: int
    ) -> tuple[list[ChangeRow], int]:
        rows = [row for row in self.store.changes.rows if _matches(row, filters)]
        rows.sort(key=lambda row: (row.event_time, row.id), reverse=True)
        return rows[offset : offset + limit], len(rows)

    def counts(self, filters: ChangeFilters, *, closed_since: datetime) -> ChangeCountsData:
        rows = [row for row in self.store.changes.rows if _matches(row, filters)]
        linked = {link.change_id for link in self.store.links.rows}
        by_status: dict[Any, int] = defaultdict(int)
        for row in rows:
            by_status[row.status] += 1
        return ChangeCountsData(
            by_status=dict(by_status),
            high_risk_open=sum(
                1
                for row in rows
                if row.risk is Risk.high and row.status not in CLOSED_CHANGE_STATUSES
            ),
            linked_to_incident=sum(1 for row in rows if row.id in linked),
            closed_since=sum(
                1
                for row in rows
                if row.status in CLOSED_CHANGE_STATUSES
                and row.decided_at is not None
                and row.decided_at >= closed_since
            ),
        )

    def last_detected_at(self) -> datetime | None:
        return max((row.detected_at for row in self.store.changes.rows), default=None)


class FakeIncidentRepository:
    def __init__(self, store: FakeStore) -> None:
        self.store = store

    def get(self, incident_id: int, *, for_update: bool = False) -> IncidentRow | None:
        return next((row for row in self.store.incidents.rows if row.id == incident_id), None)

    def find_active_by_fingerprint(self, fingerprint: str) -> IncidentRow | None:
        return next(
            (
                row
                for row in self.store.incidents.rows
                if row.fingerprint == fingerprint and row.status in ACTIVE_INCIDENT_STATUSES
            ),
            None,
        )

    def add(self, row: IncidentRow) -> IncidentRow:
        return self.store.incidents.insert(row)

    def list(
        self, filters: IncidentFilters, *, offset: int, limit: int
    ) -> tuple[list[IncidentRow], int]:
        rows = [
            row
            for row in self.store.incidents.rows
            if (not filters.statuses or row.status in filters.statuses)
            and (not filters.severity or row.severity == filters.severity)
            and (not filters.namespace or row.namespace == filters.namespace)
        ]
        rows.sort(key=lambda row: (row.opened_at, row.id), reverse=True)
        return rows[offset : offset + limit], len(rows)


class FakeIncidentChangeRepository:
    def __init__(self, store: FakeStore) -> None:
        self.store = store

    def add(self, row: IncidentChangeRow) -> IncidentChangeRow:
        return self.store.links.insert(row)

    def incidents_for_changes(
        self, change_ids: Sequence[int]
    ) -> dict[int, list[tuple[IncidentRow, Confidence]]]:
        incidents = {row.id: row for row in self.store.incidents.rows}
        result: dict[int, list[tuple[IncidentRow, Confidence]]] = defaultdict(list)
        for link in self.store.links.rows:
            if link.change_id in change_ids:
                result[link.change_id].append((incidents[link.incident_id], link.confidence))
        return result

    def changes_for_incidents(
        self, incident_ids: Sequence[int]
    ) -> dict[int, list[tuple[ChangeRow, Confidence]]]:
        changes = {row.id: row for row in self.store.changes.rows}
        result: dict[int, list[tuple[ChangeRow, Confidence]]] = defaultdict(list)
        for link in self.store.links.rows:
            if link.incident_id in incident_ids:
                result[link.incident_id].append((changes[link.change_id], link.confidence))
        return result


class FakeAgentRunRepository:
    def __init__(self, store: FakeStore) -> None:
        self.store = store

    def add(self, row: AgentRunRow) -> AgentRunRow:
        return self.store.agent_runs.insert(row)

    def get(self, run_id: int) -> AgentRunRow | None:
        return next((row for row in self.store.agent_runs.rows if row.id == run_id), None)


class FakeEvidenceRepository:
    def __init__(self, store: FakeStore) -> None:
        self.store = store

    def add(self, row: EvidenceRow) -> EvidenceRow:
        return self.store.evidence.insert(row)

    def list_for_subject(self, subject_type: SubjectType, subject_id: int) -> list[EvidenceRow]:
        runs = {
            run.id: run
            for run in self.store.agent_runs.rows
            if run.subject_type is subject_type and run.subject_id == subject_id
        }
        rows = [row for row in self.store.evidence.rows if row.agent_run_id in runs]
        return sorted(
            rows,
            key=lambda row: (runs[row.agent_run_id].started_at, row.agent_run_id, row.seq),
        )

    def count_for_run(self, run_id: int) -> int:
        return sum(1 for row in self.store.evidence.rows if row.agent_run_id == run_id)


class _LatestForSubject[T]:
    def __init__(self, table: _Table[T]) -> None:
        self.table = table

    def add(self, row: T) -> T:
        return self.table.insert(row)

    def latest_for(self, subject_type: SubjectType, subject_id: int) -> T | None:
        matches = [
            row
            for row in self.table.rows
            if row.subject_type is subject_type  # type: ignore[attr-defined]
            and row.subject_id == subject_id  # type: ignore[attr-defined]
        ]
        return max(matches, key=lambda row: row.id, default=None)  # type: ignore[attr-defined]


class FakeUnitOfWork:
    def __init__(self, store: FakeStore) -> None:
        self.store = store
        self.changes = FakeChangeRepository(store)
        self.incidents = FakeIncidentRepository(store)
        self.links = FakeIncidentChangeRepository(store)
        self.agent_runs = FakeAgentRunRepository(store)
        self.evidence = FakeEvidenceRepository(store)
        self.analyses = _LatestForSubject[AnalysisRow](store.analyses)
        self.remediations = _LatestForSubject[RemediationRow](store.remediations)

    def commit(self) -> None:
        self.store.commits += 1

    def rollback(self) -> None:
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        pass


class FakeStore:
    """Shared by every unit of work a test app creates, so tests can seed and inspect it."""

    def __init__(self) -> None:
        self.changes: _Table[ChangeRow] = _Table()
        self.incidents: _Table[IncidentRow] = _Table()
        self.links: _Table[IncidentChangeRow] = _Table()
        self.agent_runs: _Table[AgentRunRow] = _Table()
        self.evidence: _Table[EvidenceRow] = _Table()
        self.analyses: _Table[AnalysisRow] = _Table()
        self.remediations: _Table[RemediationRow] = _Table()
        self.commits = 0

    def uow(self) -> FakeUnitOfWork:
        return FakeUnitOfWork(self)
