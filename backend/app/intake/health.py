"""The intake worker's health report, every `INTAKE_REPORT_SECONDS` (60), into `intake_reports`.

- Queue backlog: the change queue's `ApproximateNumberOfMessages` (`sqs:GetQueueAttributes`).
- Oldest message age: from the SentTimestamp of the messages the consumer received since the
  last report (`QueueStats`): SQS has no queue attribute for it. With nothing queued it's 0.
- Dead-letter count: the DLQ's `ApproximateNumberOfMessages`.
- Trail logging: `cloudtrail:GetTrailStatus` → `IsLogging`.

Each call is separate: one that fails is stored as null, so the health endpoint says which part
is unknown instead of showing the last good value. When the consumer's own receive is failing,
the queue fields are null too ("SQS unreachable"), even if GetQueueAttributes still answers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.db.repositories import QueueReport, UnitOfWork
from app.intake.consumer import QueueStats
from app.services.intake_health import record_report

log = logging.getLogger(__name__)


class HealthReporter:
    def __init__(
        self,
        *,
        sqs: Any,
        cloudtrail: Any,
        queue_url: str,
        dlq_url: str | None,
        trail_name: str | None,
        stats: QueueStats,
        uow_factory: Callable[[], UnitOfWork],
        clock: Callable[[], datetime],
    ) -> None:
        self._sqs = sqs
        self._cloudtrail = cloudtrail
        self._queue_url = queue_url
        self._dlq_url = dlq_url
        self._trail_name = trail_name
        self._stats = stats
        self._uow_factory = uow_factory
        self._clock = clock

    def _messages(self, url: str | None, what: str) -> int | None:
        if not url:
            return None
        try:
            response = self._sqs.get_queue_attributes(
                QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"]
            )
            return int(response["Attributes"]["ApproximateNumberOfMessages"])
        except Exception as exc:
            log.warning("GetQueueAttributes on the %s failed (%s)", what, type(exc).__name__)
            return None

    def _trail_logging(self) -> bool | None:
        if not self._trail_name:
            return None
        try:
            return bool(self._cloudtrail.get_trail_status(Name=self._trail_name)["IsLogging"])
        except Exception as exc:
            log.warning("GetTrailStatus failed (%s)", type(exc).__name__)
            return None

    def measure(self) -> QueueReport:
        reachable, oldest_seen = self._stats.take()
        backlog = self._messages(self._queue_url, "change queue")
        if reachable is False:
            backlog = None
        age = None if backlog is None else (oldest_seen or 0)
        return QueueReport(
            queue_backlog=backlog,
            oldest_message_age_seconds=age,
            dead_letter_count=self._messages(self._dlq_url, "dead-letter queue"),
            trail_logging=self._trail_logging(),
        )

    def report_once(self) -> QueueReport:
        report = self.measure()
        with self._uow_factory() as uow:
            record_report(uow, report, now=self._clock())
        return report

    async def run(self, interval_seconds: float) -> None:
        while True:
            try:
                await asyncio.to_thread(self.report_once)
            except Exception as exc:
                # The database is down: the stale report turns health Down by itself.
                log.error("intake health report not stored (%s)", type(exc).__name__)
            await asyncio.sleep(interval_seconds)
