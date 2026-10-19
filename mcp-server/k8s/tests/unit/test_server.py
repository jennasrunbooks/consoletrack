"""The MCP surface: tool names and arguments, annotations, errors, and the probes."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastmcp import Client
from kubernetes.client import ApiException
from starlette.testclient import TestClient

from k8s_mcp.server import build_server

from .conftest import FakeKube, FakePrometheus

# product-spec.md → Triage agent and guardrails → MCP tools (k8s-mcp): names and arguments.
SPEC_TOOLS = {
    "get_workload": ["namespace", "kind", "name"],
    "get_pods": ["namespace", "selector"],
    "get_pod_logs": ["namespace", "pod", "container", "previous", "tail"],
    "get_events": ["namespace", "involved_object"],
    "get_rollout_history": ["namespace", "deployment"],
    "query_prometheus": ["promql", "minutes"],
}


def run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture
def server(settings, kube: FakeKube, prom: FakePrometheus):
    return build_server(settings, kube, prom)


def test_tools_match_the_spec_and_are_read_only(server) -> None:
    async def list_tools():
        async with Client(server) as client:
            return await client.list_tools()

    tools = {t.name: t for t in run(list_tools())}

    assert set(tools) == set(SPEC_TOOLS)
    for name, args in SPEC_TOOLS.items():
        assert list(tools[name].input_schema["properties"]) == args, name
        assert tools[name].annotations.read_only_hint is True
        assert tools[name].annotations.destructive_hint is False


def test_call_through_mcp(server, kube: FakeKube) -> None:
    kube.log_text = "boom"

    async def call():
        async with Client(server) as client:
            return await client.call_tool(
                "get_pod_logs", {"namespace": "demo", "pod": "demo-app-7d9f-abcde", "tail": 500}
            )

    result = run(call())

    assert result.structured_content["untrusted_data"] == "boom"
    assert result.structured_content["tail"] == 200


def test_refusal_reaches_the_model_as_a_tool_error(server, kube: FakeKube) -> None:
    async def call():
        async with Client(server) as client:
            return await client.call_tool(
                "get_workload",
                {"namespace": "demo", "kind": "Secret", "name": "demo-secrets"},
                raise_on_error=False,
            )

    result = run(call())

    assert result.is_error
    assert "never reads Secrets or ConfigMaps" in result.content[0].text
    assert kube.calls == []


def test_unexpected_errors_are_masked(server, kube: FakeKube) -> None:
    kube.error = RuntimeError("internal detail /var/run/secrets/token")

    async def call():
        async with Client(server) as client:
            return await client.call_tool("get_pods", {"namespace": "demo"}, raise_on_error=False)

    result = run(call())

    assert result.is_error
    assert "internal detail" not in result.content[0].text


def test_healthz_and_readyz(server, kube: FakeKube) -> None:
    with TestClient(server.http_app(path="/mcp")) as http:
        assert http.get("/healthz").json() == {"status": "ok"}
        ready = http.get("/readyz")
        assert ready.status_code == 200
        assert {c[1]["namespace"] for c in kube.calls} == {"demo", "demo-2"}

        kube.error = ApiException(status=403, reason="Forbidden")
        not_ready = http.get("/readyz")
        assert not_ready.status_code == 503
        assert not_ready.json()["checks"] == {"kubernetes": "error"}
