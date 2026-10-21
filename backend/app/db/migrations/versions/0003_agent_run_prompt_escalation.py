"""Agent runs record their prompt version and whether they were an escalation (#60).

- `agent_runs.prompt_version`: the prompt set the run used (`v2`, ...); null for earlier runs.
- `agent_runs.escalated`: the worker's one re-run with `TRIAGE_MODEL_ESCALATION` after a
  low-confidence result; at most one per incident.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("prompt_version", sa.String(16)))
    op.add_column(
        "agent_runs",
        sa.Column("escalated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "escalated")
    op.drop_column("agent_runs", "prompt_version")
