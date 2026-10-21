"""Trace context for the incident journey (#33, docs/observability.md).

- `incidents.traceparent`: the `incident.open` span's W3C traceparent, which the worker's triage
  and later alerts for the incident continue.
- `remediations.traceparent`: the `remediation.gate` span's, which the apply loop continues.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("traceparent", sa.String(64)))
    op.add_column("remediations", sa.Column("traceparent", sa.String(64)))


def downgrade() -> None:
    op.drop_column("remediations", "traceparent")
    op.drop_column("incidents", "traceparent")
