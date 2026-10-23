"""The change queue consumer: SQS long poll → parse → record → commit → delete.

A message is deleted only after its change (or canary) is committed. Anything that fails before
that (a body the parser can't read, a database error) leaves the message in the queue: SQS shows
it again after the visibility timeout, and after five receives it moves to the dead-letter
queue, which turns intake health Down. Log lines carry the SQS message ID, never the body or the
database error's text (SQLAlchemy puts the statement's parameters, the event's request
parameters, in it).

boto3 and the database are synchronous, so each receive and each batch runs in a thread
(`asyncio.to_thread`): the event loop stays free for the heartbeat and the triage run beside it.

New changes are analysed by `ChangeAnalysisLoop`, which picks them from the database
(`changes.next_for_analysis`), so a restart never loses one. The consumer only wakes it up.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from app.agents.changes import ChangeAnalysis
from app.db.repositories import UnitOfWork
from app.intake.parser import CanaryHeartbeat, Ignored, IntakeError, ParserConfig, parse
from app.services.changes import record_change
from app.services.intake_health import record_canary

log = logging.getLogger(__name__)

WAIT_SECONDS = 20
BATCH_SIZE = 10
BACKOFF_START_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 60.0


@dataclass(frozen=True)
class Message:
    message_id: str
    receipt_handle: str
    body: str
    # SQS's SentTimestamp: when EventBridge put the event on the queue.
    sent_at: datetime | None = None


class Queue(Protocol):
    def receive(self) -> list[Message]: ...
    def delete(self, message: Message) -> None: ...


class SqsQueue:
    """The change queue through boto3: ReceiveMessage and DeleteMessage only."""

    def __init__(self, client: Any, url: str, *, visibility_timeout: int) -> None:
        self._client = client
        self._url = url
        self._visibility_timeout = visibility_timeout

    def receive(self) -> list[Message]:
        response = self._client.receive_message(
            QueueUrl=self._url,
            MaxNumberOfMessages=BATCH_SIZE,
            WaitTimeSeconds=WAIT_SECONDS,
            VisibilityTimeout=self._visibility_timeout,
            # The current name and the older one: ElasticMQ (replay) may know only the latter.
            MessageSystemAttributeNames=["SentTimestamp"],
            AttributeNames=["SentTimestamp"],
        )
        return [_message(raw) for raw in response.get("Messages", [])]

    def delete(self, message: Message) -> None:
        self._client.delete_message(QueueUrl=self._url, ReceiptHandle=message.receipt_handle)


def _message(raw: dict[str, Any]) -> Message:
    sent = raw.get("Attributes", {}).get("SentTimestamp")
    return Message(
        message_id=raw["MessageId"],
        receipt_handle=raw["ReceiptHandle"],
        body=raw.get("Body", ""),
        sent_at=datetime.fromtimestamp(int(sent) / 1000, UTC) if sent else None,
    )


class QueueStats:
    """What the consumer saw, for the health reporter (another thread).

    SQS has no queue attribute for the oldest message's age (that's a CloudWatch metric, which
    the worker's role can't read), so the reporter derives it from the SentTimestamp of the
    messages received since its last report.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reachable: bool | None = None
        self._oldest_seconds: int | None = None

    def received(self, messages: list[Message], now: datetime) -> None:
        ages = [int((now - m.sent_at).total_seconds()) for m in messages if m.sent_at]
        with self._lock:
            self._reachable = True
            if ages:
                self._oldest_seconds = max(self._oldest_seconds or 0, *ages)

    def unreachable(self) -> None:
        with self._lock:
            self._reachable = False

    def take(self) -> tuple[bool | None, int | None]:
        """(reachable on the last receive, oldest age seen since the last call); resets the age."""
        with self._lock:
            result = (self._reachable, self._oldest_seconds)
            self._oldest_seconds = None
            return result


class Outcome(StrEnum):
    created = "created"
    duplicate = "duplicate"
    canary = "canary"
    ignored = "ignored"
    # Left in the queue for redelivery (and, after five receives, the dead-letter queue).
    kept = "kept"


class ChangeConsumer:
    def __init__(
        self,
        *,
        queue: Queue,
        uow_factory: Callable[[], UnitOfWork],
        parser_config: ParserConfig,
        clock: Callable[[], datetime],
        stats: QueueStats | None = None,
        on_new_change: Callable[[], None] = lambda: None,
    ) -> None:
        self._queue = queue
        self._uow_factory = uow_factory
        self._config = parser_config
        self._clock = clock
        self.stats = stats or QueueStats()
        self._on_new_change = on_new_change

    async def run(self) -> None:
        backoff = BACKOFF_START_SECONDS
        while True:
            try:
                messages = await asyncio.to_thread(self._queue.receive)
            except Exception as exc:
                # Credentials, network, a missing queue: health shows the queue unreachable.
                self.stats.unreachable()
                log.warning(
                    "change queue receive failed (%s); retrying in %.0f s",
                    type(exc).__name__,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_SECONDS)
                continue
            backoff = BACKOFF_START_SECONDS
            self.stats.received(messages, self._clock())
            if messages:
                outcomes = await asyncio.to_thread(self.process_batch, messages)
                if Outcome.created in outcomes:
                    self._on_new_change()

    def process_batch(self, messages: list[Message]) -> list[Outcome]:
        return [self.process(message) for message in messages]

    def process(self, message: Message) -> Outcome:
        try:
            parsed = parse(message.body, self._config)
        except IntakeError as exc:
            log.warning("change message %s unreadable: %s", message.message_id, exc)
            return Outcome.kept
        except Exception as exc:
            name = type(exc).__name__
            log.error("change message %s: parser failed (%s)", message.message_id, name)
            return Outcome.kept
        try:
            outcome = self._store(parsed)
        except Exception as exc:
            log.error(
                "change message %s not stored (%s); SQS will redeliver it",
                message.message_id,
                type(exc).__name__,
            )
            return Outcome.kept
        try:
            self._queue.delete(message)
        except Exception as exc:
            # Stored already: the redelivery is a duplicate and gets deleted then.
            name = type(exc).__name__
            log.warning("change message %s not deleted (%s)", message.message_id, name)
        log.info("change message %s: %s", message.message_id, outcome.value)
        return outcome

    def _store(self, parsed: Any) -> Outcome:
        if isinstance(parsed, Ignored):
            log.info("ignored a change event: %s", parsed.reason)
            return Outcome.ignored
        with self._uow_factory() as uow:
            if isinstance(parsed, CanaryHeartbeat):
                record_canary(uow, parsed.event_time)
                uow.commit()
                return Outcome.canary
            row, created = record_change(uow, parsed)
            uow.commit()
            if created:
                log.info("recorded change %s", row.id)
            return Outcome.created if created else Outcome.duplicate


class ChangeAnalysisLoop:
    """Analyse recorded changes one at a time, oldest first, from the database."""

    def __init__(
        self,
        *,
        analysis: ChangeAnalysis,
        uow_factory: Callable[[], UnitOfWork],
        clock: Callable[[], datetime],
        lease: timedelta,
        max_runs: int,
        poll_seconds: float,
    ) -> None:
        self._analysis = analysis
        self._uow_factory = uow_factory
        self._clock = clock
        self._lease = lease
        self._max_runs = max_runs
        self._poll_seconds = poll_seconds
        self._wake = asyncio.Event()

    def wake(self) -> None:
        """Called on the event loop when the consumer records a new change."""
        self._wake.set()

    def next_change(self) -> int | None:
        with self._uow_factory() as uow:
            row = uow.changes.next_for_analysis(
                lease_start=self._clock() - self._lease, max_runs=self._max_runs
            )
            return row.id if row is not None else None

    async def run_next(self) -> bool:
        change_id = self.next_change()
        if change_id is None:
            return False
        try:
            await self._analysis.analyze(change_id)
        except Exception:
            log.exception("change analysis failed change=%s", change_id)
            raise
        return True

    async def run(self) -> None:
        while True:
            try:
                busy = await self.run_next()
            except Exception:
                # run_next logged the change id and traceback; the run row's lease or `max_runs`
                # decides what happens to the change.
                log.error("change analysis iteration failed; backing off")
                busy = False
                # Back off rather than retry in a tight loop.
                self._wake.clear()
                await asyncio.sleep(self._poll_seconds)
                continue
            if not busy:
                self._wake.clear()
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=self._poll_seconds)
