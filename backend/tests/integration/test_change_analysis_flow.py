"""Change analysis against Postgres with the real unit of work.

The unit tests use a fake UoW, which never expires loaded rows. The real one rolls back on exit
without a commit, and a rollback expires every instance, so reading an ORM row after its `with`
block raised DetachedInstanceError on EKS.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, select

from app.agents.changes import ChangeAnalysis
from app.agents.runner import AgentRunner, RunLimits
from app.db.models import AgentRunRow, AnalysisRow
from app.db.session import make_uow_factory
from app.enums import AgentRunOutcome, ChangeStatus, SubjectType, Via
from app.services.changes import NewChange, record_change
from app.settings import Settings
from tests.unit.agent_fakes import FakeModelClient, submit, toolbox_factory

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
SG = "arn:aws:ec2:us-east-1:123456789012:security-group/sg-0001"


def record(uow_factory: Any) -> int:
    with uow_factory() as uow:
        row, _ = record_change(
            uow,
            NewChange(
                cloudtrail_event_id="evt-1",
                event_time=NOW - timedelta(minutes=5),
                principal_arn="arn:aws:sts::123456789012:assumed-role/demo-app-developer/jdev",
                via=Via.console,
                region="us-east-1",
                service="ec2",
                event_name="AuthorizeSecurityGroupIngress",
                resource_arn=SG,
                request_parameters={"cidr": "0.0.0.0/0"},
            ),
        )
        uow.commit()
        return row.id


def make_analysis(uow_factory: Any, model: FakeModelClient, factory: Any) -> ChangeAnalysis:
    return ChangeAnalysis(
        uow_factory=uow_factory,
        runner=AgentRunner(model, uow_factory, limits=RunLimits(), clock=lambda: NOW),
        settings=Settings(),
        toolbox_factory=factory,
        clock=lambda: NOW,
    )


async def test_analysis_runs_on_the_real_unit_of_work(clean_db: Engine) -> None:
    uow_factory = make_uow_factory(clean_db)
    change_id = record(uow_factory)
    model = FakeModelClient(
        [
            submit(
                title="SSH opened to 0.0.0.0/0",
                summary="jdev opened a port from the console.",
                root_cause=None,
                risk="high",
                risk_reason="Open to the internet.",
            )
        ]
    )
    result = await make_analysis(uow_factory, model, toolbox_factory()).analyze(change_id)

    assert result.outcome is AgentRunOutcome.succeeded
    assert f"CHG-{change_id}" in model.requests[0]["messages"][0]["content"]
    with uow_factory() as uow:
        assert uow.changes.get(change_id).status is ChangeStatus.awaiting_review
    with clean_db.connect() as connection:
        runs = connection.execute(select(AgentRunRow.outcome)).all()
        analyses = connection.execute(select(AnalysisRow.id)).all()
    assert len(runs) == 1 and len(analyses) == 1


async def test_failure_before_the_run_is_recorded_and_stops_retries(clean_db: Engine) -> None:
    uow_factory = make_uow_factory(clean_db)
    change_id = record(uow_factory)

    @asynccontextmanager
    async def broken(_: Settings) -> AsyncIterator[Any]:
        raise RuntimeError("secret detail that must not be stored")
        yield

    analysis = make_analysis(uow_factory, FakeModelClient([]), broken)
    lease_start = NOW + timedelta(hours=1)  # past the failed run's lease
    with uow_factory() as uow:
        assert uow.changes.next_for_analysis(lease_start=lease_start, max_runs=3) is not None
    await analysis.analyze(change_id)

    with clean_db.connect() as connection:
        runs = connection.execute(
            select(AgentRunRow.outcome, AgentRunRow.subject_type).where(
                AgentRunRow.subject_id == change_id
            )
        ).all()
        summaries = connection.execute(select(AnalysisRow.summary)).scalars().all()
    assert [(r.outcome, r.subject_type) for r in runs] == [
        (AgentRunOutcome.failed, SubjectType.change)
    ]
    assert len(summaries) == 1 and "RuntimeError" in summaries[0]
    assert "secret detail" not in summaries[0]  # the class name only
    # The recorded run and its analysis row take the change out of the queue: no retry loop.
    with uow_factory() as uow:
        assert uow.changes.next_for_analysis(lease_start=lease_start, max_runs=3) is None
        assert uow.changes.get(change_id).status is ChangeStatus.detected
