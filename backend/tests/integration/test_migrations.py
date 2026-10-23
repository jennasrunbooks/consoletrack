from __future__ import annotations

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect

from app.db.models import Base
from tests.integration.conftest import alembic_config

pytestmark = pytest.mark.integration

TABLES = {
    "changes",
    "incidents",
    "incident_changes",
    "agent_runs",
    "evidence",
    "analyses",
    "remediations",
    "intake_reports",
}


def test_head_matches_the_models(engine: Engine) -> None:
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == [], f"models and migrations differ: {diff}"


def test_constraints_and_indexes(engine: Engine) -> None:
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) >= TABLES
    assert {"name": "uq_changes_cloudtrail_event_id", "column_names": ["cloudtrail_event_id"]} in [
        {"name": c["name"], "column_names": c["column_names"]}
        for c in inspector.get_unique_constraints("changes")
    ]
    change_indexes = {i["name"]: i["column_names"] for i in inspector.get_indexes("changes")}
    assert change_indexes["ix_changes_status_event_time"] == ["status", "event_time"]
    assert change_indexes["ix_changes_principal_arn"] == ["principal_arn"]
    incident_indexes = {i["name"]: i for i in inspector.get_indexes("incidents")}
    assert incident_indexes["ix_incidents_status_opened_at"]["column_names"] == [
        "status",
        "opened_at",
    ]
    active = incident_indexes["uq_incidents_active_fingerprint"]
    assert active["unique"] and active["column_names"] == ["fingerprint"]
    checks = {c["name"] for c in inspector.get_check_constraints("changes")}
    assert {"ck_changes_status", "ck_changes_via", "ck_changes_risk"} <= checks


def test_downgrade_to_base_and_back(engine: Engine, database_url: str) -> None:
    config = alembic_config(database_url)
    command.downgrade(config, "base")
    assert not TABLES & set(inspect(engine).get_table_names())
    command.upgrade(config, "head")
    assert set(inspect(engine).get_table_names()) >= TABLES
