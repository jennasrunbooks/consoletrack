"""SQLAlchemy models for the seven tables in product-spec.md → Workflow and data model.

Columns beyond the spec's "key columns" are the ones `openapi.yaml` needs to render a change or
incident (region, user agent, request parameters, title, drift, labels, ...).

Enums are stored as VARCHAR with a CHECK constraint rather than native Postgres enum types, so
a later migration that adds a value is a plain constraint swap instead of `ALTER TYPE`.

`status` columns are only ever assigned by `app/services/change_state.py` and
`app/services/incident_state.py`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.enums import (
    AgentRunOutcome,
    ChangeDecision,
    ChangeStatus,
    Confidence,
    IncidentDecision,
    IncidentStatus,
    RemediationKind,
    RemediationMode,
    RemediationStatus,
    Risk,
    SubjectType,
    Via,
)

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def enum_column(enum: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChangeRow(TimestampMixin, Base):
    """One out-of-band write: the change log."""

    __tablename__ = "changes"
    __table_args__ = (
        Index("ix_changes_status_event_time", "status", "event_time"),
        Index("ix_changes_principal_arn", "principal_arn"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # CloudTrail eventID: the intake's dedupe key.
    cloudtrail_event_id: Mapped[str] = mapped_column(String(128), unique=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    principal_arn: Mapped[str] = mapped_column(String(2048))
    session_name: Mapped[str | None] = mapped_column(String(256))
    via: Mapped[Via] = mapped_column(enum_column(Via, "via"))
    source_ip: Mapped[str | None] = mapped_column(String(256))
    user_agent: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str] = mapped_column(String(32))
    service: Mapped[str] = mapped_column(String(64))
    event_name: Mapped[str] = mapped_column(String(128))
    resource_arn: Mapped[str] = mapped_column(String(2048))
    resource_name: Mapped[str | None] = mapped_column(String(512))
    tf_address: Mapped[str | None] = mapped_column(String(512))
    request_parameters: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    drift: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Written by the agent; null until then (the API builds one from event and resource).
    title: Mapped[str | None] = mapped_column(String(512))
    risk: Mapped[Risk | None] = mapped_column(enum_column(Risk, "risk"))
    status: Mapped[ChangeStatus] = mapped_column(
        enum_column(ChangeStatus, "status"), default=ChangeStatus.detected
    )
    decision: Mapped[ChangeDecision | None] = mapped_column(enum_column(ChangeDecision, "decision"))
    decided_by: Mapped[str | None] = mapped_column(String(256))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)


class IncidentRow(TimestampMixin, Base):
    """One alert episode."""

    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incidents_status_opened_at", "status", "opened_at"),
        # A re-firing alert reuses the unresolved incident with its fingerprint.
        Index(
            "ix_incidents_triage_requested_at",
            "triage_requested_at",
            postgresql_where=text("triage_requested_at IS NOT NULL"),
        ),
        Index(
            "uq_incidents_active_fingerprint",
            "fingerprint",
            unique=True,
            postgresql_where=text("status NOT IN ('resolved', 'closed')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(128))
    alertname: Mapped[str] = mapped_column(String(256))
    severity: Mapped[str | None] = mapped_column(String(64))
    namespace: Mapped[str | None] = mapped_column(String(63))
    workload: Mapped[str | None] = mapped_column(String(253))
    labels: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    annotations: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    status: Mapped[IncidentStatus] = mapped_column(
        enum_column(IncidentStatus, "status"), default=IncidentStatus.open
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision: Mapped[IncidentDecision | None] = mapped_column(
        enum_column(IncidentDecision, "decision")
    )
    decided_by: Mapped[str | None] = mapped_column(String(256))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    # The triage queue: the worker claims an incident once this time has passed, and pushes it
    # forward by a lease while the run lasts, so a crashed worker's claim expires. Null = no
    # triage pending. `triage_model` is the re-run's model override (null = TRIAGE_MODEL).
    triage_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    triage_model: Mapped[str | None] = mapped_column(String(128))


class IncidentChangeRow(TimestampMixin, Base):
    """Links a symptom (incident) to its likely cause (change)."""

    __tablename__ = "incident_changes"
    __table_args__ = (Index("ix_incident_changes_change_id", "change_id"),)

    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True
    )
    change_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("changes.id", ondelete="CASCADE"), primary_key=True
    )
    confidence: Mapped[Confidence] = mapped_column(enum_column(Confidence, "confidence"))


class AgentRunRow(TimestampMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_subject", "subject_type", "subject_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    subject_type: Mapped[SubjectType] = mapped_column(enum_column(SubjectType, "subject_type"))
    subject_id: Mapped[int] = mapped_column(BigInteger)
    model: Mapped[str] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[AgentRunOutcome] = mapped_column(
        enum_column(AgentRunOutcome, "outcome"), default=AgentRunOutcome.running
    )


class EvidenceRow(TimestampMixin, Base):
    """One tool call, already truncated and redacted."""

    __tablename__ = "evidence"
    __table_args__ = (UniqueConstraint("agent_run_id", "seq"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    agent_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("agent_runs.id", ondelete="CASCADE")
    )
    seq: Mapped[int] = mapped_column(Integer)
    tool_name: Mapped[str] = mapped_column(String(128))
    args_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result_excerpt: Mapped[str] = mapped_column(Text)


class AnalysisRow(TimestampMixin, Base):
    __tablename__ = "analyses"
    __table_args__ = (Index("ix_analyses_subject", "subject_type", "subject_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    subject_type: Mapped[SubjectType] = mapped_column(enum_column(SubjectType, "subject_type"))
    subject_id: Mapped[int] = mapped_column(BigInteger)
    agent_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("agent_runs.id", ondelete="CASCADE")
    )
    summary: Mapped[str] = mapped_column(Text)
    root_cause: Mapped[str | None] = mapped_column(Text)
    risk: Mapped[Risk | None] = mapped_column(enum_column(Risk, "risk"))
    risk_reason: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Confidence] = mapped_column(enum_column(Confidence, "confidence"))
    # The agent's flags from submit_analysis (app/agents/schema.py), e.g. stateful_workload.
    flags: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # Editable by an admin (PUT /incidents/{id}/postmortem).
    postmortem_md: Mapped[str | None] = mapped_column(Text)


class RemediationRow(TimestampMixin, Base):
    """A proposed fix and, once approved, every step of applying it.

    `status` is only ever assigned by `app/services/remediation_state.py`.
    """

    __tablename__ = "remediations"
    __table_args__ = (
        Index("ix_remediations_subject", "subject_type", "subject_id"),
        # The policy gate's rate limit and block list look up auto fixes per workload.
        Index("ix_remediations_workload", "namespace", "workload"),
        # The backend's apply queue: approved, or running with an expired lease.
        Index(
            "ix_remediations_status_pending",
            "status",
            postgresql_where=text("status IN ('approved', 'running')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    subject_type: Mapped[SubjectType] = mapped_column(enum_column(SubjectType, "subject_type"))
    subject_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[RemediationKind] = mapped_column(enum_column(RemediationKind, "kind"))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[RemediationStatus] = mapped_column(
        enum_column(RemediationStatus, "status"), default=RemediationStatus.proposed
    )
    decided_by: Mapped[str | None] = mapped_column(String(256))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The GitHub workflow run or pull request.
    external_url: Mapped[str | None] = mapped_column(String(2048))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    # The policy gate's answer: the mode, the policy that decided, and why (e.g. why a proposal
    # was downgraded to manual). Null until the gate has seen it.
    mode: Mapped[RemediationMode | None] = mapped_column(enum_column(RemediationMode, "mode"))
    policy: Mapped[str | None] = mapped_column(String(128))
    gate_reason: Mapped[str | None] = mapped_column(Text)
    # The cluster workload a kind like set_memory_limit targets; null for AWS-side kinds.
    namespace: Mapped[str | None] = mapped_column(String(63))
    workload: Mapped[str | None] = mapped_column(String(253))
    # The container's resources before and after the patch, e.g. {"limits": {"memory": "128Mi"}}.
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Every apply step in order: {"step", "at", "ok", "detail"} (app/services/remediation.py).
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # The backend that claimed it to apply holds it until then; a crashed backend's claim expires.
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
