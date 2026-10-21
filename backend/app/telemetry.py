"""OpenTelemetry for the incident journey: traces, metrics, and JSON logs that share a trace ID
(docs/observability.md).

`setup(service_name, settings)` configures the process once: `consoletrack-api` (the API and the
remediation apply loop in its lifespan) or `consoletrack-worker`. TELEMETRY_EXPORTER decides
where it goes:

- `none` (the default, and what unit tests get): spans and metrics never leave the process;
  logs still go to stdout as JSON.
- `otlp`: traces, metrics, and logs over OTLP/HTTP to OTEL_EXPORTER_OTLP_ENDPOINT, the collector.
- `console`: spans printed to stdout, for debugging.

Privacy is enforced here rather than at each call site. Every span, span event, span link, and
log record that leaves the process keeps only the keys in ALLOWED_ATTRIBUTES. Exception messages
and stack traces are dropped (the type stays), and span status descriptions are cleared.
Auto-instrumentation adds keys such as `client.address` and `user_agent.original`, and those
stop at the exporter. `tests/unit/test_telemetry_redaction.py` runs the journey and fails on any
key outside the list, or any value that looks like a credential, IP address, account ID, or ARN.

Trace context: the webhook stores `incident.open`'s W3C `traceparent` on the incident, and the
gate stores `remediation.gate`'s on the remediation. The worker and the apply loop continue from
those (`context_from`, `continuing`), so one incident is one trace.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping, MutableMapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from opentelemetry import context as otel_context
from opentelemetry import metrics, propagate, trace
from opentelemetry._logs import LogRecord, SeverityNumber, set_logger_provider
from opentelemetry.context import Context
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Link, Status

from app.settings import Settings, TelemetryExporter

API = "consoletrack-api"
WORKER = "consoletrack-worker"

# The only span, event, link, and log attribute keys that leave the process. Adding one is a
# privacy decision: docs/observability.md → Privacy lists what must never be recorded.
ALLOWED_ATTRIBUTES = frozenset(
    {
        # alert.receive. The fingerprint is hashed again; the name is the alert rule's.
        "alert.name",
        "alert.fingerprint",
        "alert.status",
        # incident.open, incident.resolve
        "incident.id",
        "incident.severity",
        "incident.state",
        "incident.resolution",
        "incident.time_to_resolve_s",
        # triage.run
        "triage.model",
        "triage.tool_calls",
        "triage.outcome",
        "triage.escalated",
        # mcp.call: the tool's name and the result's size, never its arguments or text.
        "mcp.server",
        "mcp.tool",
        "mcp.result_bytes",
        "mcp.error",
        # remediation.gate, remediation.validate / apply / verify
        "remediation.id",
        "remediation.kind",
        "remediation.mode",
        "remediation.policy",
        "remediation.result",
        # FastAPI server spans: the route template, never the URL, query, client, or headers.
        # Both HTTP semantic-convention generations, whichever the instrumentation emits.
        "http.method",
        "http.request.method",
        "http.route",
        "http.status_code",
        "http.response.status_code",
        "http.scheme",
        "url.scheme",
        "http.flavor",
        "network.protocol.version",
        # FastMCP's client spans around each MCP request.
        "mcp.method.name",
        "mcp.protocol.version",
        "gen_ai.tool.name",
        "fastmcp.component.key",
        "fastmcp.component.type",
        "fastmcp.server.name",
        "fastmcp.span.seam",
        "error.type",
        # Exception events and logged exceptions: the type only, never the message or stack.
        "exception.type",
        "exception.escaped",
        # Log records.
        "logger.name",
        "code.function.name",
        "code.line.number",
    }
)
# Metric label keys (`tests/unit/test_telemetry_redaction.py` checks every exported point).
METRIC_LABELS = frozenset(
    {"route", "method", "status_code", "severity", "resolution", "kind", "mode", "result"}
)

tracer = trace.get_tracer("consoletrack")
meter = metrics.get_meter("consoletrack")

_configured = False


def allowed(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    return {key: value for key, value in (attributes or {}).items() if key in ALLOWED_ATTRIBUTES}


def scrub_span(span: ReadableSpan) -> ReadableSpan:
    """The span with only allowlisted attributes, events, and links, and no status text."""
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=allowed(span.attributes),
        events=[Event(e.name, allowed(e.attributes), e.timestamp) for e in span.events],
        links=[Link(link.context, allowed(link.attributes)) for link in span.links],
        kind=span.kind,
        # Descriptions carry exception text (FastMCP sets str(error)).
        status=Status(span.status.status_code),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class AllowlistSpanExporter(SpanExporter):
    """Wraps the real exporter: what it receives has been through `scrub_span`."""

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._inner.export([scrub_span(span) for span in spans])

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._inner.force_flush(timeout_millis)


# --- setup -------------------------------------------------------------------------------------


def setup(service_name: str, settings: Settings) -> None:
    """Configure tracing, metrics, and logging for this process; later calls do nothing."""
    if _configured:
        return
    resource = Resource.create({"service.name": service_name})
    tracer_provider = TracerProvider(resource=resource)
    readers: list[MetricReader] = []
    logger_provider = None
    if settings.telemetry_exporter is TelemetryExporter.otlp:
        # Imported here: only `otlp` needs the exporters (and their protobuf stack).
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        tracer_provider.add_span_processor(
            BatchSpanProcessor(AllowlistSpanExporter(OTLPSpanExporter()))
        )
        readers.append(
            PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=15_000)
        )
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    elif settings.telemetry_exporter is TelemetryExporter.console:
        tracer_provider.add_span_processor(
            SimpleSpanProcessor(AllowlistSpanExporter(ConsoleSpanExporter()))
        )
    install(
        tracer_provider, MeterProvider(resource=resource, metric_readers=readers), logger_provider
    )
    configure_logging(settings.log_level, logger_provider)


def install(
    tracer_provider: TracerProvider,
    meter_provider: MeterProvider,
    logger_provider: LoggerProvider | None,
) -> None:
    """Set the global providers, once per process. Tests call it with in-memory exporters before
    any app is built, so `setup` then leaves them alone."""
    global _configured
    _configured = True
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(meter_provider)
    if logger_provider is not None:
        set_logger_provider(logger_provider)


def instrument_app(app: FastAPI) -> None:
    """Server spans for every request but the probes and /metrics, and `ct_http_*` metrics.

    The instrumentation's own HTTP metrics are off (a no-op meter): `HttpMetrics` records the
    names and labels in docs/observability.md instead.
    """
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls=",".join(UNMEASURED_PATHS),
        meter_provider=NoOpMeterProvider(),
        exclude_spans=["receive", "send"],
    )
    app.add_middleware(HttpMetrics)


# --- trace context -----------------------------------------------------------------------------


def current_traceparent() -> str | None:
    """The current span's W3C `traceparent`, to store on a row; None outside a recorded span."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier.get("traceparent")


def context_from(traceparent: str | None) -> Context | None:
    """A context whose parent is the stored span; None (the current context) when unset."""
    if not traceparent:
        return None
    return propagate.extract({"traceparent": traceparent}, context=Context())


@contextmanager
def continuing(traceparent: str | None) -> Iterator[None]:
    """Make the stored span the parent of every span started inside the block."""
    context = context_from(traceparent)
    if context is None:
        yield
        return
    token = otel_context.attach(context)
    try:
        yield
    finally:
        otel_context.detach(token)


def hashed(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


# --- metrics -----------------------------------------------------------------------------------
# Exported with their Prometheus names as-is: the collector's remote write adds no suffixes
# (observability/otel-collector/kind.yaml). Labels stay low-cardinality (METRIC_LABELS).

HTTP_REQUESTS = meter.create_counter(
    "ct_http_requests_total", description="API requests by route template, method, and status."
)
HTTP_DURATION = meter.create_histogram(
    "ct_http_request_duration_seconds",
    unit="s",
    description="API request duration.",
    explicit_bucket_boundaries_advisory=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
INCIDENTS_OPENED = meter.create_counter(
    "ct_incidents_opened_total", description="Incidents opened by a firing alert, by severity."
)
INCIDENTS_RESOLVED = meter.create_counter(
    "ct_incidents_resolved_total",
    description="Incidents resolved: auto_fixed, approved_fix, or manual (no ConsoleTrack fix).",
)
TRIAGE_DURATION = meter.create_histogram(
    "ct_triage_duration_seconds",
    unit="s",
    description="One incident's triage, including an escalated re-run.",
    explicit_bucket_boundaries_advisory=(1, 2, 5, 10, 20, 30, 45, 60, 90, 120, 180, 300),
)
TRIAGE_TOOL_CALLS = meter.create_histogram(
    "ct_triage_tool_calls",
    description="Evidence tool calls in one incident's triage.",
    explicit_bucket_boundaries_advisory=(0, 1, 2, 3, 4, 6, 8, 10, 12, 16, 24),
)
REMEDIATIONS = meter.create_counter(
    "ct_remediations_total", description="Finished remediations by kind, mode, and result."
)
TIME_TO_RESOLVE = meter.create_histogram(
    "ct_time_to_resolve_seconds",
    unit="s",
    description="From the alert's start to its resolved notification.",
    explicit_bucket_boundaries_advisory=(
        60,
        120,
        300,
        600,
        900,
        1800,
        3600,
        7200,
        14_400,
        43_200,
        86_400,
    ),
)

# Probes and the scrape endpoint: no spans, no request metrics.
UNMEASURED_PATHS = ("/healthz", "/readyz", "/metrics")
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
SEVERITIES = frozenset({"critical", "warning", "info"})

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


def severity_label(value: str | None) -> str:
    return value if value in SEVERITIES else "other"


class HttpMetrics:
    """`ct_http_requests_total` and `ct_http_request_duration_seconds` per route template.

    Plain ASGI: the router stores the matched route in the scope, so the label is `/api/v1/
    incidents/{incident_id}`, never an ID; an unmatched path is `unmatched`.
    """

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in UNMEASURED_PATHS:
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = 500

        async def send_status(message: MutableMapping[str, Any]) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_status)
        finally:
            route = getattr(scope.get("route"), "path", None) or "unmatched"
            method = scope["method"] if scope["method"] in METHODS else "other"
            labels = {"route": route, "method": method, "status_code": str(status)}
            HTTP_REQUESTS.add(1, labels)
            HTTP_DURATION.record(time.perf_counter() - started, labels)


# --- logs --------------------------------------------------------------------------------------

# Loggers whose records never go through the OTel log pipeline: the exporters' own HTTP stack
# would otherwise log about exporting its logs.
_NOT_EXPORTED = ("opentelemetry", "urllib3", "requests")
_SEVERITY = (
    (logging.CRITICAL, SeverityNumber.FATAL),
    (logging.ERROR, SeverityNumber.ERROR),
    (logging.WARNING, SeverityNumber.WARN),
    (logging.INFO, SeverityNumber.INFO),
)


def log_fields(record: logging.LogRecord) -> dict[str, Any]:
    """`extra=` fields whose names are allowlisted, e.g. `extra={"incident.id": 3}`."""
    return {key: value for key, value in vars(record).items() if key in ALLOWED_ATTRIBUTES}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, event, trace_id and span_id inside a span,
    and allowlisted fields. Stack traces stay on stdout; the OTel pipeline gets the type only."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        span = trace.get_current_span().get_span_context()
        if span.is_valid:
            entry["trace_id"] = trace.format_trace_id(span.trace_id)
            entry["span_id"] = trace.format_span_id(span.span_id)
        entry.update(log_fields(record))
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


class OtelLogHandler(logging.Handler):
    """Log records to the OTel log pipeline, built from allowlisted fields only (the SDK's own
    LoggingHandler copies every `extra` and the exception message and stack trace)."""

    def __init__(self, provider: LoggerProvider) -> None:
        super().__init__()
        self._provider = provider
        self.addFilter(lambda record: not record.name.startswith(_NOT_EXPORTED))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            attributes: dict[str, Any] = {
                "logger.name": record.name,
                "code.function.name": record.funcName,
                "code.line.number": record.lineno,
                **log_fields(record),
            }
            if record.exc_info and record.exc_info[0] is not None:
                attributes["exception.type"] = record.exc_info[0].__name__
            severity = next((s for level, s in _SEVERITY if record.levelno >= level), None)
            self._provider.get_logger(record.name).emit(
                LogRecord(
                    timestamp=int(record.created * 1e9),
                    observed_timestamp=time.time_ns(),
                    context=otel_context.get_current(),
                    severity_text=record.levelname,
                    severity_number=severity or SeverityNumber.DEBUG,
                    body=record.getMessage(),
                    attributes=allowed(attributes),
                )
            )
        except Exception:
            self.handleError(record)


def configure_logging(level: str, logger_provider: LoggerProvider | None = None) -> None:
    """JSON lines on stdout for every logger, plus the OTel log pipeline when it exports."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handlers: list[logging.Handler] = [handler]
    if logger_provider is not None:
        handlers.append(OtelLogHandler(logger_provider))
    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(level.upper())
