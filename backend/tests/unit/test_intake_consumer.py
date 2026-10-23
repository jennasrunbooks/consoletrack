"""The change queue consumer, the analysis pickup, and the health reporter, on fakes."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.agents.runner import RunResult
from app.db.models import AgentRunRow, AnalysisRow
from app.enums import AgentRunOutcome, ChangeStatus, Confidence, SubjectType
from app.intake.consumer import (
    ChangeAnalysisLoop,
    ChangeConsumer,
    Message,
    Outcome,
    QueueStats,
    SqsQueue,
)
from app.intake.health import HealthReporter
from app.intake.parser import ParserConfig
from app.services.intake_health import DbIntakeStatus
from tests.unit.conftest import NOW
from tests.unit.fakes import FakeStore, FakeUnitOfWork

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cloudtrail"


def message(name: str, n: int = 0, *, age_seconds: int = 5) -> Message:
    return Message(
        message_id=f"msg-{n}-{name}",
        receipt_handle=f"rh-{n}",
        body=(FIXTURES / name).read_text(),
        sent_at=NOW - timedelta(seconds=age_seconds),
    )


class Journal:
    """The order of commits and deletes across the fake queue and units of work."""

    def __init__(self) -> None:
        self.events: list[str] = []


class FakeQueue:
    def __init__(self, journal: Journal, batches: list[list[Message] | Exception]) -> None:
        self.journal = journal
        self.batches = batches
        self.deleted: list[str] = []
        self.fail_delete = False

    def receive(self) -> list[Message]:
        if not self.batches:
            raise asyncio.CancelledError
        batch = self.batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return batch

    def delete(self, message: Message) -> None:
        if self.fail_delete:
            raise RuntimeError("delete failed")
        self.journal.events.append(f"delete {message.message_id}")
        self.deleted.append(message.message_id)


class JournalUow(FakeUnitOfWork):
    def __init__(self, store: FakeStore, journal: Journal, fail_commit: bool) -> None:
        super().__init__(store)
        self.journal = journal
        self.fail_commit = fail_commit

    def commit(self) -> None:
        if self.fail_commit:
            raise RuntimeError("INSERT ... [parameters: {'request_parameters': 'secret-value'}]")
        self.journal.events.append("commit")
        super().commit()


class Harness:
    def __init__(self, batches: list[list[Message] | Exception] | None = None) -> None:
        self.store = FakeStore()
        self.journal = Journal()
        self.queue = FakeQueue(self.journal, batches or [])
        self.fail_commit = False
        self.wakes = 0
        self.consumer = ChangeConsumer(
            queue=self.queue,
            uow_factory=lambda: JournalUow(self.store, self.journal, self.fail_commit),
            parser_config=ParserConfig(),
            clock=lambda: NOW,
            on_new_change=self._wake,
        )

    def _wake(self) -> None:
        self.wakes += 1


def test_delete_comes_after_the_commit() -> None:
    h = Harness()
    msg = message("console-authorize-sg-ingress.json")
    assert h.consumer.process(msg) is Outcome.created
    assert h.journal.events == ["commit", f"delete {msg.message_id}"]
    [row] = h.store.changes.rows
    assert row.status is ChangeStatus.detected


def test_a_duplicate_event_makes_one_change() -> None:
    h = Harness()
    outcomes = h.consumer.process_batch(
        [
            message("console-authorize-sg-ingress.json", 1),
            message("duplicate-console-authorize-sg-ingress.json", 2),
        ]
    )
    assert outcomes == [Outcome.created, Outcome.duplicate]
    assert len(h.store.changes.rows) == 1
    assert len(h.queue.deleted) == 2


def test_a_database_error_keeps_the_message_and_logs_no_parameters(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = Harness()
    h.fail_commit = True
    msg = message("cli-put-role-policy.json")
    assert h.consumer.process(msg) is Outcome.kept
    assert h.queue.deleted == []
    assert msg.message_id in caplog.text
    assert "secret-value" not in caplog.text
    assert "demo-app-s3-read" not in caplog.text


def test_an_unreadable_body_is_left_for_the_dead_letter_queue(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = Harness()
    msg = Message(message_id="msg-bad", receipt_handle="rh", body='{"x": "s3cr3t"')
    assert h.consumer.process(msg) is Outcome.kept
    assert h.queue.deleted == []
    assert h.journal.events == []
    assert "msg-bad" in caplog.text
    assert "s3cr3t" not in caplog.text


def test_canary_updates_the_heartbeat_and_makes_no_change() -> None:
    h = Harness()
    assert h.consumer.process(message("canary-create-tags.json")) is Outcome.canary
    assert h.store.changes.rows == []
    assert h.journal.events[0] == "commit"
    with h.store.uow() as uow:
        assert DbIntakeStatus().snapshot(uow).last_canary_at is not None


def test_ignored_events_are_deleted_without_a_write() -> None:
    h = Harness()
    msg = message("excluded/pipeline-gh-session.json")
    assert h.consumer.process(msg) is Outcome.ignored
    assert h.journal.events == [f"delete {msg.message_id}"]


def test_a_failed_delete_still_counts_as_stored() -> None:
    h = Harness()
    h.queue.fail_delete = True
    assert h.consumer.process(message("cli-put-role-policy.json")) is Outcome.created
    # The redelivery is a duplicate and is deleted then.
    h.queue.fail_delete = False
    assert h.consumer.process(message("cli-put-role-policy.json", 2)) is Outcome.duplicate
    assert len(h.store.changes.rows) == 1


@pytest.mark.anyio
async def test_run_wakes_analysis_once_per_batch_with_new_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.intake.consumer.asyncio.sleep", fake_sleep)
    h = Harness(
        [
            [],
            [
                message("console-authorize-sg-ingress.json", 1),
                message("cli-put-role-policy.json", 2),
            ],
            [message("duplicate-console-authorize-sg-ingress.json", 3)],
            RuntimeError("no credentials"),
            RuntimeError("no credentials"),
            RuntimeError("no credentials"),
            [message("canary-create-tags.json", 4)],
            RuntimeError("no credentials"),
        ]
    )
    with pytest.raises(asyncio.CancelledError):
        await h.consumer.run()
    assert h.wakes == 1
    assert len(h.store.changes.rows) == 2
    # Capped exponential backoff, reset by a successful receive.
    assert sleeps == [1.0, 2.0, 4.0, 1.0]
    assert h.consumer.stats.take() == (False, 5)


def test_stats_track_the_oldest_message_seen() -> None:
    stats = QueueStats()
    assert stats.take() == (None, None)
    stats.received([message("cli-put-role-policy.json", age_seconds=30)], NOW)
    stats.received([message("cli-put-role-policy.json", age_seconds=700)], NOW)
    stats.received([], NOW)
    assert stats.take() == (True, 700)
    assert stats.take() == (True, None)


def test_sqs_queue_maps_messages() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def receive_message(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(("receive", kwargs))
            sent = str(int(NOW.timestamp() * 1000))
            return {
                "Messages": [
                    {
                        "MessageId": "m1",
                        "ReceiptHandle": "r1",
                        "Body": "{}",
                        "Attributes": {"SentTimestamp": sent},
                    }
                ]
            }

        def delete_message(self, **kwargs: Any) -> None:
            self.calls.append(("delete", kwargs))

    client = Client()
    queue = SqsQueue(client, "https://sqs.example/q", visibility_timeout=60)
    [msg] = queue.receive()
    assert msg == Message("m1", "r1", "{}", NOW)
    queue.delete(msg)
    receive, delete = client.calls
    assert receive[1]["WaitTimeSeconds"] == 20
    assert receive[1]["MaxNumberOfMessages"] == 10
    assert receive[1]["VisibilityTimeout"] == 60
    assert delete == ("delete", {"QueueUrl": "https://sqs.example/q", "ReceiptHandle": "r1"})


# --- analysis pickup ---------------------------------------------------------------------------


class FakeAnalysis:
    def __init__(self, store: FakeStore) -> None:
        self.store = store
        self.analyzed: list[int] = []

    async def analyze(self, change_id: int) -> RunResult:
        self.analyzed.append(change_id)
        run = self.store.agent_runs.insert(
            AgentRunRow(
                subject_type=SubjectType.change,
                subject_id=change_id,
                model="m",
                started_at=NOW,
                outcome=AgentRunOutcome.succeeded,
            )
        )
        self.store.analyses.insert(
            AnalysisRow(
                subject_type=SubjectType.change,
                subject_id=change_id,
                agent_run_id=run.id,
                summary="s",
                confidence=Confidence.high,
                flags=[],
            )
        )
        return RunResult(run.id, AgentRunOutcome.succeeded, None, "ok", 0, 0, 0)


def analysis_loop(store: FakeStore, analysis: Any, **kwargs: Any) -> ChangeAnalysisLoop:
    return ChangeAnalysisLoop(
        analysis=analysis,
        uow_factory=store.uow,
        clock=lambda: NOW,
        lease=kwargs.get("lease", timedelta(minutes=5)),
        max_runs=kwargs.get("max_runs", 3),
        poll_seconds=0,
    )


@pytest.mark.anyio
async def test_each_new_change_is_analysed_once_oldest_first() -> None:
    h = Harness()
    h.consumer.process_batch(
        [
            message("console-authorize-sg-ingress.json", 1),
            message("cli-put-role-policy.json", 2),
            message("duplicate-console-authorize-sg-ingress.json", 3),
        ]
    )
    analysis = FakeAnalysis(h.store)
    loop = analysis_loop(h.store, analysis)
    assert await loop.run_next()
    assert await loop.run_next()
    assert not await loop.run_next()
    assert analysis.analyzed == [1, 2]


@pytest.mark.anyio
async def test_a_crashed_run_is_retried_after_its_lease_and_then_given_up() -> None:
    h = Harness()
    h.consumer.process(message("cli-put-role-policy.json"))
    loop = analysis_loop(h.store, FakeAnalysis(h.store), max_runs=2)

    def crashed_run(started: timedelta) -> None:
        h.store.agent_runs.insert(
            AgentRunRow(
                subject_type=SubjectType.change,
                subject_id=1,
                model="m",
                started_at=NOW - started,
                outcome=AgentRunOutcome.running,
            )
        )

    crashed_run(timedelta(minutes=1))
    assert loop.next_change() is None  # still in its lease
    h.store.agent_runs.rows[0].started_at = NOW - timedelta(minutes=6)
    assert loop.next_change() == 1
    crashed_run(timedelta(minutes=10))
    assert loop.next_change() is None  # two runs: the limit


def test_a_reviewed_change_is_not_analysed() -> None:
    h = Harness()
    h.consumer.process(message("cli-put-role-policy.json"))
    h.store.changes.rows[0].status = ChangeStatus.accepted
    assert analysis_loop(h.store, FakeAnalysis(h.store)).next_change() is None


@pytest.mark.anyio
async def test_the_analysis_loop_survives_errors() -> None:
    class Failing:
        calls = 0

        async def analyze(self, change_id: int) -> RunResult:
            Failing.calls += 1
            if Failing.calls > 2:
                raise asyncio.CancelledError
            raise RuntimeError("model down")

    h = Harness()
    h.consumer.process(message("cli-put-role-policy.json"))
    loop = analysis_loop(h.store, Failing())
    with pytest.raises(asyncio.CancelledError):
        await loop.run()
    assert Failing.calls == 3


# --- health reporter ---------------------------------------------------------------------------


class FakeSqs:
    def __init__(self, counts: dict[str, int | Exception]) -> None:
        self.counts = counts

    def get_queue_attributes(self, *, QueueUrl: str, AttributeNames: list[str]) -> dict:  # noqa: N803
        assert AttributeNames == ["ApproximateNumberOfMessages"]
        count = self.counts[QueueUrl]
        if isinstance(count, Exception):
            raise count
        return {"Attributes": {"ApproximateNumberOfMessages": str(count)}}


class FakeTrail:
    def __init__(self, logging: bool | Exception) -> None:
        self.logging = logging

    def get_trail_status(self, *, Name: str) -> dict:  # noqa: N803
        assert Name == "ct-trail"
        if isinstance(self.logging, Exception):
            raise self.logging
        return {"IsLogging": self.logging}


def reporter(
    store: FakeStore,
    counts: dict[str, int | Exception],
    logging: bool | Exception = True,
    stats: QueueStats | None = None,
) -> HealthReporter:
    return HealthReporter(
        sqs=FakeSqs(counts),
        cloudtrail=FakeTrail(logging),
        queue_url="q",
        dlq_url="dlq",
        trail_name="ct-trail",
        stats=stats or QueueStats(),
        uow_factory=store.uow,
        clock=lambda: NOW,
    )


def snapshot(store: FakeStore):
    with store.uow() as uow:
        return DbIntakeStatus().snapshot(uow)


def test_report_with_everything_reachable() -> None:
    store = FakeStore()
    stats = QueueStats()
    stats.received([message("cli-put-role-policy.json", age_seconds=42)], NOW)
    reporter(store, {"q": 3, "dlq": 0}, stats=stats).report_once()
    result = snapshot(store)
    assert (result.queue_backlog, result.oldest_message_age_seconds) == (3, 42)
    assert (result.dead_letter_count, result.trail_logging, result.reported_at) == (0, True, NOW)


def test_empty_queue_has_age_zero() -> None:
    store = FakeStore()
    reporter(store, {"q": 0, "dlq": 0}).report_once()
    assert snapshot(store).oldest_message_age_seconds == 0


def test_a_failed_call_stores_null_for_that_field_only() -> None:
    store = FakeStore()
    reporter(store, {"q": 1, "dlq": 0}).report_once()
    failing = reporter(store, {"q": RuntimeError("throttled"), "dlq": 2}, logging=RuntimeError())
    failing.report_once()
    result = snapshot(store)
    assert result.queue_backlog is None
    assert result.oldest_message_age_seconds is None
    assert result.dead_letter_count == 2
    assert result.trail_logging is None


def test_an_unreachable_consumer_nulls_the_queue_fields() -> None:
    store = FakeStore()
    stats = QueueStats()
    stats.unreachable()
    reporter(store, {"q": 0, "dlq": 0}, stats=stats).report_once()
    assert snapshot(store).queue_backlog is None


@pytest.mark.anyio
async def test_reporter_loop_survives_a_database_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def fake_sleep(seconds: float) -> None:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr("app.intake.health.asyncio.sleep", fake_sleep)

    def broken_uow() -> FakeUnitOfWork:
        raise RuntimeError("database down")

    r = reporter(FakeStore(), {"q": 0, "dlq": 0})
    r._uow_factory = broken_uow
    with pytest.raises(asyncio.CancelledError):
        await r.run(60)
    assert calls == 2


def test_fixture_bodies_are_eventbridge_events() -> None:
    for path in FIXTURES.rglob("*.json"):
        assert json.loads(path.read_text())["detail-type"] == "AWS API Call via CloudTrail"
