"""Triage queue and agent outcome: what the agent loop (#10) needs.

- `incidents.triage_requested_at` and `triage_model`: the worker's queue and lease, and the
  re-run's model override.
- `analyses.flags`: the agent's flags (`stateful_workload`, `migration`, ...).
- `agent_runs.outcome` gains `limit_reached` (the tool-call limit).
- `remediations.kind` gains `set_memory_limit` (the OOM proposal; #34 validates and applies it).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AGENT_RUN_OUTCOME_0001 = ("running", "succeeded", "failed", "timed_out", "budget_exceeded")
AGENT_RUN_OUTCOME = (*AGENT_RUN_OUTCOME_0001, "limit_reached")
REMEDIATION_KIND_0001 = ("cluster_patch", "rollback", "revert_workflow", "codify_pr")
REMEDIATION_KIND = (*REMEDIATION_KIND_0001, "set_memory_limit")


def swap_check(table: str, column: str, values: tuple[str, ...]) -> None:
    name = op.f(f"ck_{table}_{column}")
    allowed = ", ".join(f"'{value}'" for value in values)
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({allowed})")


def upgrade() -> None:
    op.add_column("incidents", sa.Column("triage_requested_at", sa.DateTime(timezone=True)))
    op.add_column("incidents", sa.Column("triage_model", sa.String(128)))
    op.create_index(
        "ix_incidents_triage_requested_at",
        "incidents",
        ["triage_requested_at"],
        postgresql_where=sa.text("triage_requested_at IS NOT NULL"),
    )
    op.add_column(
        "analyses",
        sa.Column("flags", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    swap_check("agent_runs", "outcome", AGENT_RUN_OUTCOME)
    swap_check("remediations", "kind", REMEDIATION_KIND)


def downgrade() -> None:
    # Rows that use the new values would violate the old constraints; there are none to keep
    # once the agent loop is rolled back, so they go.
    op.execute("DELETE FROM remediations WHERE kind = 'set_memory_limit'")
    op.execute("UPDATE agent_runs SET outcome = 'failed' WHERE outcome = 'limit_reached'")
    swap_check("remediations", "kind", REMEDIATION_KIND_0001)
    swap_check("agent_runs", "outcome", AGENT_RUN_OUTCOME_0001)
    op.drop_column("analyses", "flags")
    op.drop_index("ix_incidents_triage_requested_at", table_name="incidents")
    op.drop_column("incidents", "triage_model")
    op.drop_column("incidents", "triage_requested_at")
