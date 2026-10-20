"""The remediation queries the policy gate and the apply loop run, against Postgres."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.db.models import RemediationRow
from app.db.sql import SqlUnitOfWork
from app.enums import RemediationKind, RemediationMode, RemediationStatus, SubjectType

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC).replace(microsecond=0)
KINDS = {RemediationKind.set_memory_limit}


def add(uow: SqlUnitOfWork, **fields: object) -> RemediationRow:
    values: dict[str, object] = {
        "subject_type": SubjectType.incident,
        "subject_id": 1,
        "kind": RemediationKind.set_memory_limit,
        "payload_json": {"namespace": "demo", "deployment": "demo-app"},
        "status": RemediationStatus.approved,
    }
    values.update(fields)
    return uow.remediations.add(RemediationRow(**values))


def test_next_to_apply_takes_approved_and_expired_leases_once(clean_db: Engine) -> None:
    with SqlUnitOfWork(Session(clean_db)) as uow:
        leased = add(uow, status=RemediationStatus.running, lease_until=NOW + timedelta(minutes=5))
        expired = add(uow, status=RemediationStatus.running, lease_until=NOW - timedelta(seconds=1))
        approved = add(uow)
        add(uow, kind=RemediationKind.revert_workflow)  # not a kind the loop applies
        add(uow, status=RemediationStatus.proposed)
        uow.commit()
        ids = (leased.id, expired.id, approved.id)

    # Two backends at once: each locks a different row (SKIP LOCKED).
    with SqlUnitOfWork(Session(clean_db)) as first, SqlUnitOfWork(Session(clean_db)) as second:
        a = first.remediations.next_to_apply(NOW, KINDS)
        b = second.remediations.next_to_apply(NOW, KINDS)
        assert a is not None and b is not None
        assert {a.id, b.id} == {ids[1], ids[2]}


def test_auto_for_workload_and_the_new_columns(clean_db: Engine) -> None:
    with SqlUnitOfWork(Session(clean_db)) as uow:
        auto = add(
            uow,
            mode=RemediationMode.auto,
            namespace="demo",
            workload="demo-app",
            decided_at=NOW,
            before_json={"limits": {"memory": "128Mi"}},
            after_json={"limits": {"memory": "256Mi"}},
            steps=[{"step": "patch", "at": NOW.isoformat(), "ok": True, "detail": "x"}],
        )
        add(uow, mode=RemediationMode.manual, namespace="demo", workload="demo-app")
        add(uow, mode=RemediationMode.auto, namespace="demo", workload="other")
        uow.commit()
        auto_id = auto.id

    with SqlUnitOfWork(Session(clean_db)) as uow:
        rows = uow.remediations.auto_for_workload("demo", "demo-app")
        assert [row.id for row in rows] == [auto_id]
        assert rows[0].steps[0]["step"] == "patch"
        assert rows[0].after_json == {"limits": {"memory": "256Mi"}}
        # The column default: an empty list, not null.
        plain = add(uow)
        uow.commit()
        assert uow.remediations.get(plain.id).steps == []
