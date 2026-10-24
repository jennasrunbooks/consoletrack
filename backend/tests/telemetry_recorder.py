"""Telemetry for every test suite: in-memory exporters, installed by tests/conftest.py before
any app is built.

`app.telemetry.setup` then leaves the process alone, so tests never export anything, never
reconfigure the root logger, and stay offline. `tests/unit/test_telemetry_redaction.py` reads
what was recorded through the `recorded` fixture.

Two span exporters see every span: `raw` gets them as the code made them, `exported` after the
allowlist, exactly as the OTLP exporter would.
"""

from __future__ import annotations

from dataclasses import dataclass

from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import Counter, Histogram, MeterProvider
from opentelemetry.sdk.metrics.export import AggregationTemporality, InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import telemetry


@dataclass(frozen=True)
class Recorded:
    raw: InMemorySpanExporter
    exported: InMemorySpanExporter
    logs: InMemoryLogRecordExporter
    metrics: InMemoryMetricReader
    logger_provider: LoggerProvider

    def clear(self) -> None:
        self.raw.clear()
        self.exported.clear()
        self.logs.clear()
        self.metrics.get_metrics_data()  # collect, so the next read starts from here


def _install() -> Recorded:
    raw, exported = InMemorySpanExporter(), InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(raw))
    tracer_provider.add_span_processor(
        SimpleSpanProcessor(telemetry.AllowlistSpanExporter(exported))
    )
    # Delta: each read holds only what was recorded since the last one (`clear`).
    delta = AggregationTemporality.DELTA
    reader = InMemoryMetricReader(preferred_temporality={Counter: delta, Histogram: delta})
    logs = InMemoryLogRecordExporter()
    logger_provider = LoggerProvider()
    logger_provider.add_log_record_processor(SimpleLogRecordProcessor(logs))
    telemetry.install(tracer_provider, MeterProvider(metric_readers=[reader]), logger_provider)
    return Recorded(raw, exported, logs, reader, logger_provider)


RECORDED = _install()
