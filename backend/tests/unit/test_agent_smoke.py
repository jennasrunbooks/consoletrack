"""`make triage-smoke`'s request and pass/fail rule, on the scripted fake (no network)."""

from __future__ import annotations

from typing import Any

import anthropic
import httpx2
import pytest

from app.agents.model import ModelError
from app.agents.schema import TOOL_NAME
from app.agents.smoke import CANNED, NUDGE, build_request, report, run_smoke
from app.settings import Settings
from tests.unit.agent_fakes import FakeModelClient, call, submit, turn

pytestmark = pytest.mark.anyio

K8S_TOOLS = [
    {"name": name, "description": f"k8s-mcp {name}", "input_schema": {"type": "object"}}
    for name in (
        "get_workload",
        "get_pods",
        "get_pod_logs",
        "get_events",
        "get_rollout_history",
        "query_prometheus",
    )
]
SETTINGS = Settings(triage_model="claude-haiku-5-5")
PROPOSAL = {
    "kind": "set_memory_limit",
    "namespace": "demo",
    "deployment": "demo-app",
    "container": "demo-app",
    "memory_limit": "256Mi",
    "reason": "OOMKilled at 128Mi.",
}


def test_request_is_the_runners_shape() -> None:
    request = build_request(K8S_TOOLS, SETTINGS)
    assert request["model"] == "claude-haiku-5-5"
    assert "untrusted data" in request["system"].lower()
    assert [t["name"] for t in request["tools"]][-1] == TOOL_NAME
    assert len(request["tools"]) == 7
    user, assistant, results = request["messages"]
    assert "DemoAppHighErrorRate" in user["content"]
    tool_uses = [b for b in assistant["content"] if b["type"] == "tool_use"]
    tool_results = [b for b in results["content"] if b["type"] == "tool_result"]
    assert [b["id"] for b in tool_uses] == [b["tool_use_id"] for b in tool_results]
    assert len(tool_uses) == len(CANNED) == 3
    assert "OOMKilled" in tool_results[0]["content"]
    assert results["content"][-1] == {"type": "text", "text": NUDGE}


def test_request_needs_the_canned_tools() -> None:
    with pytest.raises(ValueError, match="get_pod_logs"):
        build_request([t for t in K8S_TOOLS if t["name"] != "get_pod_logs"], SETTINGS)


async def test_a_valid_submit_passes() -> None:
    model = FakeModelClient([submit(proposed_remediation=PROPOSAL)])
    result = await run_smoke(model, K8S_TOOLS, SETTINGS)
    assert result.ok, result.message
    assert len(model.requests) == 1
    text = report(result, "claude-haiku-5-5")
    assert text.startswith("triage-smoke: PASS") and "256Mi" in text


async def test_no_submit_fails() -> None:
    result = await run_smoke(
        FakeModelClient([turn(call("query_prometheus", {"promql": "x"}))]), K8S_TOOLS, SETTINGS
    )
    assert not result.ok and "query_prometheus" in result.message
    assert report(result, "m").startswith("triage-smoke: FAIL")


async def test_an_invalid_submit_fails() -> None:
    bad: dict[str, Any] = {**PROPOSAL, "kind": "patch_secret"}
    result = await run_smoke(
        FakeModelClient([submit(proposed_remediation=bad)]), K8S_TOOLS, SETTINGS
    )
    assert not result.ok and "doesn't validate" in result.message


async def test_an_api_rejection_shows_the_api_message() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    cause = anthropic.BadRequestError(
        "tools.6.input_schema: unsupported keyword",
        response=httpx2.Response(400, request=request),
        body=None,
    )
    error = ModelError("Anthropic API error 400")
    error.__cause__ = cause
    result = await run_smoke(FakeModelClient([error]), K8S_TOOLS, SETTINGS)
    assert not result.ok
    assert "unsupported keyword" in result.message
