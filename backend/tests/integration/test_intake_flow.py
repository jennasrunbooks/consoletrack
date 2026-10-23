"""The change intake end to end: every recorded fixture → ElasticMQ → the consumer → Postgres.

ElasticMQ runs from Testcontainers with Compose's `compose/elasticmq.conf` (same queue names and
redrive as AWS), so this needs Docker: CI runs it (`make test-integration`).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
import pytest
from botocore import UNSIGNED
from botocore.config import Config
from sqlalchemy import Engine

from app.db.models import AgentRunRow
from app.db.repositories import ChangeFilters, QueueReport
from app.db.session import make_uow_factory
from app.enums import AgentRunOutcome, ChangeStatus, SubjectType
from app.intake.consumer import ChangeConsumer, Outcome, SqsQueue
from app.intake.parser import ParserConfig
from app.intake.replay import LOCAL_DLQ, fixture_files, local_queue_url, send_fixtures
from app.services.intake_health import DbIntakeStatus, record_canary, record_report

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 10, 17, 13, 0, tzinfo=UTC)
# Matched fixtures minus the duplicate and the canary.
EXPECTED_CHANGES = len(fixture_files()) - 2


@pytest.fixture(scope="module")
def sqs() -> Iterator[tuple[object, str]]:
    from testcontainers.core.container import DockerContainer

    container = (
        DockerContainer("softwaremill/elasticmq-native:1.7.1")
        .with_exposed_ports(9324)
        .with_volume_mapping(str(REPO / "compose" / "elasticmq.conf"), "/opt/elasticmq.conf", "ro")
    )
    with container:
        endpoint = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(9324)}"
        client = boto3.client(
            "sqs",
            endpoint_url=endpoint,
            region_name="us-east-1",
            config=Config(signature_version=UNSIGNED),
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                client.list_queues()
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        yield client, endpoint


def backlog(client, url: str) -> int:
    attrs = client.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    return int(attrs["ApproximateNumberOfMessages"]) + int(
        attrs["ApproximateNumberOfMessagesNotVisible"]
    )


def test_replay_every_fixture_end_to_end(sqs, clean_db: Engine) -> None:
    client, endpoint = sqs
    url = local_queue_url(endpoint)
    sent = send_fixtures(client, url, fixture_files())
    uow_factory = make_uow_factory(clean_db)
    consumer = ChangeConsumer(
        queue=SqsQueue(client, url, visibility_timeout=30),
        uow_factory=uow_factory,
        parser_config=ParserConfig(),
        clock=lambda: datetime.now(UTC),
    )
    outcomes: list[Outcome] = []
    while len(outcomes) < sent:
        messages = consumer._queue.receive()
        assert messages, f"queue drained after {len(outcomes)} of {sent}"
        outcomes += consumer.process_batch(messages)

    assert outcomes.count(Outcome.created) == EXPECTED_CHANGES
    assert outcomes.count(Outcome.duplicate) == 1
    assert outcomes.count(Outcome.canary) == 1
    assert Outcome.kept not in outcomes
    assert backlog(client, url) == 0
    assert backlog(client, local_queue_url(endpoint, LOCAL_DLQ)) == 0

    with uow_factory() as uow:
        rows, total = uow.changes.list(ChangeFilters(), offset=0, limit=100)
        assert total == EXPECTED_CHANGES
        assert {row.status for row in rows} == {ChangeStatus.detected}
        assert all(row.risk is not None for row in rows)
        snapshot = DbIntakeStatus().snapshot(uow)
    assert snapshot.last_canary_at == datetime(2026, 10, 17, 12, 18, tzinfo=UTC)


def test_intake_report_upserts(clean_db: Engine) -> None:
    uow_factory = make_uow_factory(clean_db)
    with uow_factory() as uow:
        record_canary(uow, NOW - timedelta(minutes=5))
        uow.commit()
    with uow_factory() as uow:
        record_report(uow, QueueReport(4, 30, 0, True), now=NOW)
        record_canary(uow, NOW - timedelta(minutes=30))  # older: kept out
        uow.commit()
    with uow_factory() as uow:
        record_report(uow, QueueReport(None, None, 1, None), now=NOW + timedelta(minutes=1))
        snapshot = DbIntakeStatus().snapshot(uow)
    assert snapshot.last_canary_at == NOW - timedelta(minutes=5)
    assert (snapshot.queue_backlog, snapshot.oldest_message_age_seconds) == (None, None)
    assert (snapshot.dead_letter_count, snapshot.trail_logging) == (1, None)
    assert snapshot.reported_at == NOW + timedelta(minutes=1)


def test_next_for_analysis_on_postgres(sqs, clean_db: Engine) -> None:
    client, endpoint = sqs
    url = local_queue_url(endpoint)
    files = [p for p in fixture_files() if p.name.startswith(("cli-", "console-"))]
    send_fixtures(client, url, files)
    uow_factory = make_uow_factory(clean_db)
    consumer = ChangeConsumer(
        queue=SqsQueue(client, url, visibility_timeout=30),
        uow_factory=uow_factory,
        parser_config=ParserConfig(),
        clock=lambda: NOW,
    )
    seen = 0
    while seen < len(files):
        messages = consumer._queue.receive()
        assert messages
        seen += len(consumer.process_batch(messages))

    lease_start = NOW - timedelta(minutes=5)
    with uow_factory() as uow:
        first = uow.changes.next_for_analysis(lease_start=lease_start, max_runs=2)
        assert first is not None
        first_id = first.id
        uow.agent_runs.add(
            AgentRunRow(
                subject_type=SubjectType.change,
                subject_id=first_id,
                model="m",
                started_at=NOW,
                outcome=AgentRunOutcome.running,
            )
        )
        uow.commit()
    with uow_factory() as uow:
        second = uow.changes.next_for_analysis(lease_start=lease_start, max_runs=2)
        assert second is not None and second.id > first_id
    # After the lease the crashed run's change comes back first.
    with uow_factory() as uow:
        again = uow.changes.next_for_analysis(lease_start=NOW + timedelta(seconds=1), max_runs=2)
        assert again is not None and again.id == first_id
        other = uow.changes.next_for_analysis(lease_start=NOW + timedelta(seconds=1), max_runs=1)
        assert other is not None and other.id != first_id
