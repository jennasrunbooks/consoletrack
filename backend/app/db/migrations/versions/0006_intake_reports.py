"""The intake worker's health report (#9): `intake_reports`, one row.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intake_reports",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("last_canary_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("queue_backlog", sa.Integer(), nullable=True),
        sa.Column("oldest_message_age_seconds", sa.Integer(), nullable=True),
        sa.Column("dead_letter_count", sa.Integer(), nullable=True),
        sa.Column("trail_logging", sa.Boolean(), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("id = 1", name=op.f("ck_intake_reports_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_intake_reports")),
    )


def downgrade() -> None:
    op.drop_table("intake_reports")
