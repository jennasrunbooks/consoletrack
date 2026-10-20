"""Remediation apply and verify: what the policy gate and the backend's apply path (#34) record.

- `remediations.mode`, `policy`, `gate_reason`: the gate's decision and why.
- `remediations.namespace`, `workload`: the target, for the per-workload rate limit and block.
- `remediations.before_json`, `after_json`, `steps`: the values the patch changed and every step
  of applying and verifying it.
- `remediations.decided_at`: when it was approved (the rate limit's clock).
- `remediations.lease_until`: the backend's claim while it applies.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REMEDIATION_MODE = ("manual", "auto")


def upgrade() -> None:
    allowed = ", ".join(f"'{value}'" for value in REMEDIATION_MODE)
    op.add_column("remediations", sa.Column("mode", sa.String(32)))
    op.create_check_constraint(op.f("ck_remediations_mode"), "remediations", f"mode IN ({allowed})")
    op.add_column("remediations", sa.Column("policy", sa.String(128)))
    op.add_column("remediations", sa.Column("gate_reason", sa.Text()))
    op.add_column("remediations", sa.Column("namespace", sa.String(63)))
    op.add_column("remediations", sa.Column("workload", sa.String(253)))
    op.add_column("remediations", sa.Column("decided_at", sa.DateTime(timezone=True)))
    op.add_column("remediations", sa.Column("before_json", JSONB()))
    op.add_column("remediations", sa.Column("after_json", JSONB()))
    op.add_column(
        "remediations",
        sa.Column("steps", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    op.add_column("remediations", sa.Column("lease_until", sa.DateTime(timezone=True)))
    op.create_index("ix_remediations_workload", "remediations", ["namespace", "workload"])
    op.create_index(
        "ix_remediations_status_pending",
        "remediations",
        ["status"],
        postgresql_where=sa.text("status IN ('approved', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("ix_remediations_status_pending", table_name="remediations")
    op.drop_index("ix_remediations_workload", table_name="remediations")
    for column in (
        "lease_until",
        "steps",
        "after_json",
        "before_json",
        "decided_at",
        "workload",
        "namespace",
        "gate_reason",
        "policy",
    ):
        op.drop_column("remediations", column)
    op.drop_constraint(op.f("ck_remediations_mode"), "remediations", type_="check")
    op.drop_column("remediations", "mode")
