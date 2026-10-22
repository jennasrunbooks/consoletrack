"""k8s-mcp's spans: FastMCP's server span continues the caller's trace, and only allowlisted
attributes leave the process (no PromQL, no arguments, no results, no error text)."""

from __future__ import annotations

import asyncio
import re

from fastmcp import Client
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from k8s_mcp.server import build_server
from k8s_mcp.telemetry import ALLOWED_ATTRIBUTES, AllowlistSpanExporter

from .conftest import FakeKube, FakePrometheus

EXPORTED = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(AllowlistSpanExporter(EXPORTED)))
trace.set_tracer_provider(_provider)

PROMQL = 'container_memory_working_set_bytes{namespace="demo", pod="secret-pod-name"}'


def test_tool_calls_join_the_callers_trace_and_export_only_allowlisted_keys(
    settings, kube: FakeKube, prom: FakePrometheus
) -> None:
    EXPORTED.clear()
    server = build_server(settings, kube, prom)

    async def call() -> None:
        async with Client(server) as client:
            with trace.get_tracer("test").start_as_current_span("mcp.call"):
                await client.call_tool("query_prometheus", {"promql": PROMQL}, raise_on_error=False)
                await client.call_tool(
                    "get_pods", {"namespace": "kube-system"}, raise_on_error=False
                )

    asyncio.run(call())
    spans = EXPORTED.get_finished_spans()
    (caller,) = [s for s in spans if s.name == "mcp.call"]
    tool_spans = [s for s in spans if s.name.startswith("tools/call")]
    assert tool_spans, [s.name for s in spans]
    assert {s.context.trace_id for s in tool_spans} == {caller.context.trace_id}
    for span in spans:
        assert set(span.attributes or {}) <= ALLOWED_ATTRIBUTES, (span.name, span.attributes)
        for event in span.events:
            assert set(event.attributes or {}) <= ALLOWED_ATTRIBUTES
        assert span.status.description is None
        text = f"{span.name} {dict(span.attributes or {})}"
        assert not re.search(r"secret-pod-name|container_memory|kube-system", text), text
