"""OpenTelemetry for k8s-mcp (docs/observability.md): spans for each MCP request and JSON logs
with the trace ID.

FastMCP opens the server spans itself (`tools/call get_pods`) and continues the caller's trace
from the request's `_meta.traceparent`, so a tool call joins the incident's trace under the
worker's `mcp.call`. This module only configures the SDK and enforces the allowlist; it mirrors
`backend/app/telemetry.py`, which this package can't import.

TELEMETRY_EXPORTER: `none` (default: nothing leaves the process), `otlp` (OTLP/HTTP to
OTEL_EXPORTER_OTLP_ENDPOINT), or `console`. Exported spans keep only ALLOWED_ATTRIBUTES, with no
exception messages, stack traces, or status descriptions: tool arguments (PromQL, pod names in
a query) and results never reach telemetry.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, TextIO

from opentelemetry import trace
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

SERVICE_NAME = "k8s-mcp"

ALLOWED_ATTRIBUTES = frozenset(
    {
        # FastMCP's server spans.
        "mcp.method.name",
        "mcp.protocol.version",
        "gen_ai.tool.name",
        "fastmcp.server.name",
        "fastmcp.component.key",
        "fastmcp.component.type",
        "fastmcp.span.seam",
        "error.type",
        # Exception events: the type only.
        "exception.type",
        "exception.escaped",
    }
)

_configured = False


def allowed(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    return {key: value for key, value in (attributes or {}).items() if key in ALLOWED_ATTRIBUTES}


def scrub_span(span: ReadableSpan) -> ReadableSpan:
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=allowed(span.attributes),
        events=[Event(e.name, allowed(e.attributes), e.timestamp) for e in span.events],
        links=[Link(link.context, allowed(link.attributes)) for link in span.links],
        kind=span.kind,
        # FastMCP puts str(error) in the description.
        status=Status(span.status.status_code),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class AllowlistSpanExporter(SpanExporter):
    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._inner.export([scrub_span(span) for span in spans])

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._inner.force_flush(timeout_millis)


class JsonFormatter(logging.Formatter):
    """ts, level, logger, event, and trace_id / span_id inside a span."""

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
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def setup(exporter: str, *, stream: TextIO = sys.stdout) -> None:
    """Configure tracing and JSON logging once. With the stdio transport, `stream` must be
    stderr: stdout carries the MCP protocol."""
    global _configured
    if _configured:
        return
    _configured = True
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    if exporter == "otlp":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(AllowlistSpanExporter(OTLPSpanExporter())))
    elif exporter == "console":
        provider.add_span_processor(
            SimpleSpanProcessor(AllowlistSpanExporter(ConsoleSpanExporter(out=sys.stderr)))
        )
    trace.set_tracer_provider(provider)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
