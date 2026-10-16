"""Prometheus metrics served at /metrics (product-spec.md → Metrics and Grafana).

Each app gets its own registry, so tests can build several apps in one process. The intake
gauges are refreshed from the intake health computation on every scrape.

Unknown values: timestamps are 0 ("never"), so `time() - x` is large and the
ConsoleTrackCanaryMissing alert fires; queue age and dead-letter count are NaN, which no alert
threshold matches, because the `down` state already covers an unreachable queue.
"""

from __future__ import annotations

from datetime import datetime

from prometheus_client import CollectorRegistry, Gauge, generate_latest

from app.enums import IntakeState
from app.schemas import IntakeHealth

NAN = float("nan")


def _timestamp(value: datetime | None) -> float:
    return value.timestamp() if value is not None else 0.0


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.last_event = Gauge(
            "consoletrack_intake_last_event_timestamp_seconds",
            "Unix time of the last change event received (excluding the canary); 0 if never.",
            registry=self.registry,
        )
        self.canary_last_seen = Gauge(
            "consoletrack_canary_last_seen_timestamp_seconds",
            "Unix time the canary last arrived through the intake; 0 if never.",
            registry=self.registry,
        )
        self.oldest_message_age = Gauge(
            "consoletrack_sqs_oldest_message_age_seconds",
            "Age of the oldest message in the change queue; NaN if unknown.",
            registry=self.registry,
        )
        self.dlq_messages = Gauge(
            "consoletrack_dlq_messages",
            "Messages in the change queue's dead-letter queue; NaN if unknown.",
            registry=self.registry,
        )
        self.intake_state = Gauge(
            "consoletrack_intake_state",
            "1 for the current intake state (live, delayed, down, replay), 0 for the others.",
            ["state"],
            registry=self.registry,
        )

    def observe_intake(self, health: IntakeHealth) -> None:
        self.last_event.set(_timestamp(health.last_event_at))
        self.canary_last_seen.set(_timestamp(health.last_canary_at))
        age = health.oldest_message_age_seconds
        self.oldest_message_age.set(NAN if age is None else age)
        dlq = health.dead_letter_count
        self.dlq_messages.set(NAN if dlq is None else dlq)
        for state in IntakeState:
            self.intake_state.labels(state=state.value).set(1 if state is health.state else 0)

    def render(self) -> bytes:
        return generate_latest(self.registry)
