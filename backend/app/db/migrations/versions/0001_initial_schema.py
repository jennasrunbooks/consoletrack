"""Initial schema: the seven tables in product-spec.md → Workflow and data model.

Enum values are frozen here on purpose rather than imported from app.enums: adding a value
later means a new migration that swaps the CHECK constraint.

Revision ID: 0001
Revises:
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHANGE_STATUS = (
    "detected",
    "awaiting_review",
    "reverting",
    "reverted",
    "accepted",
    "codifying",
    "codified",
    "failed",
)
VIA = ("console", "cli", "cloudshell", "sdk", "terraform_outside_pipeline")
LEVEL = ("low", "medium", "high")  # Risk and Confidence
CHANGE_DECISION = ("revert", "accept", "codify")
INCIDENT_STATUS = ("open", "triaging", "awaiting_review", "remediating", "resolved", "closed")
INCIDENT_DECISION = ("resolved", "rejected")
SUBJECT_TYPE = ("change", "incident")
AGENT_RUN_OUTCOME = ("running", "succeeded", "failed", "timed_out", "budget_exceeded")
REMEDIATION_KIND = ("cluster_patch", "rollback", "revert_workflow", "codify_pr")
REMEDIATION_STATUS = ("proposed", "approved", "rejected", "running", "succeeded", "failed")


def enum(column: str, values: tuple[str, ...]) -> sa.CheckConstraint:
    """CHECK named ck_<table>_<column> through the metadata naming convention."""
    allowed = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=column)


def timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "changes",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("cloudtrail_event_id", sa.String(128), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "detected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("principal_arn", sa.String(2048), nullable=False),
        sa.Column("session_name", sa.String(256)),
        sa.Column("via", sa.String(32), nullable=False),
        sa.Column("source_ip", sa.String(256)),
        sa.Column("user_agent", sa.Text()),
        sa.Column("region", sa.String(32), nullable=False),
        sa.Column("service", sa.String(64), nullable=False),
        sa.Column("event_name", sa.String(128), nullable=False),
        sa.Column("resource_arn", sa.String(2048), nullable=False),
        sa.Column("resource_name", sa.String(512)),
        sa.Column("tf_address", sa.String(512)),
        sa.Column("request_parameters", JSONB()),
        sa.Column("drift", JSONB()),
        sa.Column("title", sa.String(512)),
        sa.Column("risk", sa.String(32)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("decision", sa.String(32)),
        sa.Column("decided_by", sa.String(256)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("reason", sa.Text()),
        *timestamps(),
        sa.UniqueConstraint("cloudtrail_event_id", name="uq_changes_cloudtrail_event_id"),
        enum("via", VIA),
        enum("risk", LEVEL),
        enum("status", CHANGE_STATUS),
        enum("decision", CHANGE_DECISION),
    )
    op.create_index("ix_changes_status_event_time", "changes", ["status", "event_time"])
    op.create_index("ix_changes_principal_arn", "changes", ["principal_arn"])

    op.create_table(
        "incidents",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("alertname", sa.String(256), nullable=False),
        sa.Column("severity", sa.String(64)),
        sa.Column("namespace", sa.String(63)),
        sa.Column("workload", sa.String(253)),
        sa.Column("labels", JSONB(), nullable=False),
        sa.Column("annotations", JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("decision", sa.String(32)),
        sa.Column("decided_by", sa.String(256)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("reason", sa.Text()),
        *timestamps(),
        enum("status", INCIDENT_STATUS),
        enum("decision", INCIDENT_DECISION),
    )
    op.create_index("ix_incidents_status_opened_at", "incidents", ["status", "opened_at"])
    # A re-firing alert reuses the unresolved incident with the same fingerprint.
    op.create_index(
        "uq_incidents_active_fingerprint",
        "incidents",
        ["fingerprint"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('resolved', 'closed')"),
    )

    op.create_table(
        "incident_changes",
        sa.Column(
            "incident_id",
            sa.BigInteger(),
            sa.ForeignKey("incidents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "change_id",
            sa.BigInteger(),
            sa.ForeignKey("changes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("confidence", sa.String(32), nullable=False),
        *timestamps(),
        enum("confidence", LEVEL),
    )
    op.create_index("ix_incident_changes_change_id", "incident_changes", ["change_id"])

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.BigInteger(), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("outcome", sa.String(32), nullable=False),
        *timestamps(),
        enum("subject_type", SUBJECT_TYPE),
        enum("outcome", AGENT_RUN_OUTCOME),
    )
    op.create_index("ix_agent_runs_subject", "agent_runs", ["subject_type", "subject_id"])

    op.create_table(
        "evidence",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "agent_run_id",
            sa.BigInteger(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("args_json", JSONB(), nullable=False),
        sa.Column("result_excerpt", sa.Text(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("agent_run_id", "seq", name="uq_evidence_agent_run_id_seq"),
    )

    op.create_table(
        "analyses",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "agent_run_id",
            sa.BigInteger(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("root_cause", sa.Text()),
        sa.Column("risk", sa.String(32)),
        sa.Column("risk_reason", sa.Text()),
        sa.Column("confidence", sa.String(32), nullable=False),
        sa.Column("postmortem_md", sa.Text()),
        *timestamps(),
        enum("subject_type", SUBJECT_TYPE),
        enum("risk", LEVEL),
        enum("confidence", LEVEL),
    )
    op.create_index("ix_analyses_subject", "analyses", ["subject_type", "subject_id"])

    op.create_table(
        "remediations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload_json", JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("decided_by", sa.String(256)),
        sa.Column("external_url", sa.String(2048)),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        *timestamps(),
        enum("subject_type", SUBJECT_TYPE),
        enum("kind", REMEDIATION_KIND),
        enum("status", REMEDIATION_STATUS),
    )
    op.create_index("ix_remediations_subject", "remediations", ["subject_type", "subject_id"])


def downgrade() -> None:
    # Dependents first; dropping a table drops its indexes and constraints.
    for table in (
        "remediations",
        "analyses",
        "evidence",
        "agent_runs",
        "incident_changes",
        "incidents",
        "changes",
    ):
        op.drop_table(table)
