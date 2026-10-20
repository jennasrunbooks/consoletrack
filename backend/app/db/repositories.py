"""Repository interfaces: the only way services reach stored data.

`app/db/sql.py` implements them on a SQLAlchemy session; `tests/unit/fakes.py` implements them
in memory for unit tests (spec: SQL behavior is tested only against real Postgres, never SQLite).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, Self

from app.db.models import (
    AgentRunRow,
    AnalysisRow,
    ChangeRow,
    EvidenceRow,
    IncidentChangeRow,
    IncidentRow,
    RemediationRow,
)
from app.enums import ChangeStatus, Confidence, IncidentStatus, Risk, SubjectType, Via


@dataclass(frozen=True)
class ChangeFilters:
    """GET /api/v1/changes filters. Lists combine with OR, fields with AND; empty = no filter."""

    statuses: tuple[ChangeStatus, ...] = ()
    risks: tuple[Risk, ...] = ()
    vias: tuple[Via, ...] = ()
    principal: str | None = None
    service: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    q: str | None = None

    def without_status(self) -> ChangeFilters:
        return ChangeFilters(
            risks=self.risks,
            vias=self.vias,
            principal=self.principal,
            service=self.service,
            since=self.since,
            until=self.until,
            q=self.q,
        )


@dataclass(frozen=True)
class IncidentFilters:
    statuses: tuple[IncidentStatus, ...] = ()
    severity: str | None = None
    namespace: str | None = None


@dataclass
class ChangeCountsData:
    by_status: dict[ChangeStatus, int] = field(default_factory=dict)
    high_risk_open: int = 0
    linked_to_incident: int = 0
    closed_since: int = 0


class ChangeRepository(Protocol):
    def get(self, change_id: int, *, for_update: bool = False) -> ChangeRow | None: ...
    def get_by_event_id(self, cloudtrail_event_id: str) -> ChangeRow | None: ...
    def add(self, row: ChangeRow) -> ChangeRow: ...
    def list(
        self, filters: ChangeFilters, *, offset: int, limit: int
    ) -> tuple[list[ChangeRow], int]:
        """Newest first by event_time; returns (page, total matching)."""
        ...

    def counts(self, filters: ChangeFilters, *, closed_since: datetime) -> ChangeCountsData:
        """Counts over every change matching `filters` (callers drop the status filter)."""
        ...

    def last_detected_at(self) -> datetime | None: ...


class IncidentRepository(Protocol):
    def get(self, incident_id: int, *, for_update: bool = False) -> IncidentRow | None: ...
    def find_active_by_fingerprint(self, fingerprint: str) -> IncidentRow | None: ...
    def add(self, row: IncidentRow) -> IncidentRow: ...
    def list(
        self, filters: IncidentFilters, *, offset: int, limit: int
    ) -> tuple[list[IncidentRow], int]:
        """Newest first by opened_at; returns (page, total matching)."""
        ...

    def next_for_triage(self, now: datetime) -> IncidentRow | None:
        """The active incident whose `triage_requested_at` is oldest and not after `now`, locked
        (`FOR UPDATE SKIP LOCKED`, so two workers never claim the same one)."""
        ...


class IncidentChangeRepository(Protocol):
    def add(self, row: IncidentChangeRow) -> IncidentChangeRow: ...
    def incidents_for_changes(
        self, change_ids: Sequence[int]
    ) -> dict[int, list[tuple[IncidentRow, Confidence]]]: ...
    def changes_for_incidents(
        self, incident_ids: Sequence[int]
    ) -> dict[int, list[tuple[ChangeRow, Confidence]]]: ...


class AgentRunRepository(Protocol):
    def add(self, row: AgentRunRow) -> AgentRunRow: ...
    def get(self, run_id: int) -> AgentRunRow | None: ...
    def any_escalated(self, subject_type: SubjectType, subject_id: int) -> bool:
        """Whether the subject already had an escalated run."""
        ...


class EvidenceRepository(Protocol):
    def add(self, row: EvidenceRow) -> EvidenceRow: ...
    def list_for_subject(self, subject_type: SubjectType, subject_id: int) -> list[EvidenceRow]:
        """Every run's tool calls for the subject, oldest run first, then by seq."""
        ...

    def count_for_run(self, run_id: int) -> int: ...


class AnalysisRepository(Protocol):
    def add(self, row: AnalysisRow) -> AnalysisRow: ...
    def latest_for(self, subject_type: SubjectType, subject_id: int) -> AnalysisRow | None: ...


class RemediationRepository(Protocol):
    def add(self, row: RemediationRow) -> RemediationRow: ...
    def latest_for(self, subject_type: SubjectType, subject_id: int) -> RemediationRow | None: ...


class UnitOfWork(Protocol):
    """One request's (or one worker job's) repositories, committed together."""

    changes: ChangeRepository
    incidents: IncidentRepository
    links: IncidentChangeRepository
    agent_runs: AgentRunRepository
    evidence: EvidenceRepository
    analyses: AnalysisRepository
    remediations: RemediationRepository

    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def __enter__(self) -> Self: ...
    def __exit__(self, *exc: object) -> None: ...
