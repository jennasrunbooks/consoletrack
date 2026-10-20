"""`python -m k8s_mcp.tool_definitions`: the tool list `make triage-smoke` sends to the model."""

from __future__ import annotations

import pytest

from k8s_mcp.settings import Settings
from k8s_mcp.tool_definitions import definitions


@pytest.mark.anyio
async def test_lists_the_six_tools_without_a_cluster() -> None:
    tools = await definitions(Settings(k8s_allowed_namespaces=["demo"]))
    assert [tool["name"] for tool in tools] == [
        "get_workload",
        "get_pods",
        "get_pod_logs",
        "get_events",
        "get_rollout_history",
        "query_prometheus",
    ]
    for tool in tools:
        assert tool["description"]
        assert tool["input_schema"]["type"] == "object"
