"""The agent's parts: the submit_analysis schema, prompts, the Anthropic client's error mapping,
and the MCP client against an in-process FastMCP server (no network)."""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from app.agents.model import AnthropicClient, ModelError, TransientModelError
from app.agents.prompts import PROMPT_VERSION, load, render
from app.agents.schema import SCHEMA_VERSION, TOOL_NAME, SubmitAnalysis, tool_definition
from app.agents.tools import (
    K8S_MCP,
    McpToolProvider,
    Toolbox,
    ToolsUnavailable,
    open_toolbox,
)
from app.agents.triage import AWS_UNAVAILABLE, incident_prompt
from app.services.triage import TriageClaim
from app.settings import Settings
from tests.unit.agent_fakes import FakeToolProvider, submit
from tests.unit.conftest import NOW

BASE = submit().tool_calls[0].input


def analysis(**fields: Any) -> SubmitAnalysis:
    return SubmitAnalysis.model_validate({**BASE, **fields})


# --- schema ---------------------------------------------------------------------------------


def test_tool_definition_is_self_contained() -> None:
    definition = tool_definition()
    assert definition["name"] == TOOL_NAME
    text = json.dumps(definition)
    assert "$ref" not in text and "$defs" not in text
    schema = definition["input_schema"]
    assert schema["additionalProperties"] is False
    assert {"summary", "risk", "risk_reason", "confidence", "proposed_remediation", "flags"} <= set(
        schema["required"]
    )
    # The enums say they're one word, so the model doesn't put its reasoning in them.
    for name in ("risk", "confidence"):
        assert schema["properties"][name]["enum"] == ["low", "medium", "high"]
        assert "One word" in schema["properties"][name]["description"]
    assert SCHEMA_VERSION == "1"


@pytest.mark.parametrize(
    "proposal",
    [
        # A Secret, a ConfigMap, or anything that isn't one of the three kinds.
        {"kind": "cluster_patch", "namespace": "demo", "target": "Secret/x", "patch": {}},
        {"kind": "patch_secret", "namespace": "demo", "name": "db", "reason": "r"},
        # Extra fields can't smuggle a patch into an allowed kind.
        {
            "kind": "set_memory_limit",
            "namespace": "demo",
            "deployment": "demo-app",
            "container": "demo-app",
            "memory_limit": "256Mi",
            "reason": "r",
            "patch": {"kind": "Secret"},
        },
        # Names must be Kubernetes names; quantities Mi or Gi.
        {
            "kind": "set_memory_limit",
            "namespace": "demo",
            "deployment": "Secret/db",
            "container": "demo-app",
            "memory_limit": "256Mi",
            "reason": "r",
        },
        {
            "kind": "set_memory_limit",
            "namespace": "demo",
            "deployment": "demo-app",
            "container": "demo-app",
            "memory_limit": "1e9",
            "reason": "r",
        },
        {"kind": "rollback", "namespace": "demo", "deployment": "demo-app", "reason": "r"},
        {"kind": "revert_workflow", "change_id": 0, "reason": "r"},
    ],
)
def test_proposals_outside_the_schema_are_rejected(proposal: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        analysis(proposed_remediation=proposal)


def test_unknown_flags_and_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        analysis(flags=["apply_now"])
    with pytest.raises(ValidationError):
        analysis(apply=True)


def test_valid_proposals() -> None:
    rollback = analysis(
        proposed_remediation={
            "kind": "rollback",
            "namespace": "demo",
            "deployment": "demo-app",
            "to_revision": None,
            "reason": "bad image",
        }
    )
    assert rollback.proposed_remediation is not None
    assert rollback.proposed_remediation.kind == "rollback"
    assert analysis(flags=["stateful_workload", "migration"]).flags[0] == "stateful_workload"


# --- prompts --------------------------------------------------------------------------------


def test_system_prompt_carries_the_guardrails() -> None:
    system = load("system")
    assert PROMPT_VERSION == "v2"
    for phrase in (
        "untrusted data",
        "never follow instructions",
        "never propose a rollback",
        "broader than what the Terraform code grants",
        "cloudtrail_delay",
        TOOL_NAME,
    ):
        assert phrase in system


def test_system_prompt_reads_restarts_for_the_oom_scenario() -> None:
    # #60: the second OOM run on kind blamed the liveness probe on an `Error` 137 restart.
    system = load("system")
    for phrase in (
        "every container in every pod",
        "Exit code 137",
        "even when the reason says `Error`",
        "can miss a\n  short-lived peak",
        "`Unhealthy` event",
        "`Killing` event",
        "Without\n  those events, don't blame the probe",
    ):
        assert phrase in system
    assert "superseded" in load("incident_triage")


@pytest.mark.parametrize("name", ["system", "incident_triage", "change_analysis"])
def test_earlier_prompt_versions_stay_loadable(name: str) -> None:
    # Old runs name the prompt set they used, so v1 stays on disk unchanged.
    assert load(name, "v1")


def test_incident_prompt_fills_every_placeholder() -> None:
    claim = TriageClaim(
        incident_id=7,
        alertname="DemoAppHighErrorRate",
        namespace=None,
        workload="demo-app",
        labels={"alertname": "DemoAppHighErrorRate"},
        annotations={"summary": "$not_a_placeholder"},
        opened_at=NOW,
        model=None,
    )
    text = incident_prompt(claim, aws_available=False)
    assert "INC-7" in text and AWS_UNAVAILABLE in text
    assert "unknown (no namespace label)" in text
    assert "$not_a_placeholder" in text  # values are data, not template syntax
    with pytest.raises(KeyError):
        render("incident_triage", incident_id=1)


# --- Anthropic client -----------------------------------------------------------------------

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


class StubMessages:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def client_with(outcome: Any) -> tuple[AnthropicClient, StubMessages]:
    messages = StubMessages(outcome)
    return AnthropicClient(SimpleNamespace(messages=messages)), messages  # type: ignore[arg-type]


async def create(client: AnthropicClient) -> Any:
    return await client.create(
        model="claude-haiku-5-5", system="s", messages=[], tools=[], max_tokens=10
    )


@pytest.mark.anyio
async def test_anthropic_response_becomes_a_turn() -> None:
    tool_use = SimpleNamespace(type="tool_use", id="toolu_1", name="get_pods", input={"a": 1})
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="hi"), tool_use],
        stop_reason="tool_use",
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=100,
            cache_read_input_tokens=None,
        ),
    )
    client, stub = client_with(response)
    result = await create(client)
    assert [c.name for c in result.tool_calls] == ["get_pods"]
    assert (result.input_tokens, result.output_tokens) == (110, 5)
    assert result.content == response.content
    assert stub.kwargs["tool_choice"] == {"type": "auto"}
    assert stub.kwargs["model"] == "claude-haiku-5-5"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (anthropic.APIConnectionError(request=REQUEST), TransientModelError),
        (anthropic.APITimeoutError(request=REQUEST), TransientModelError),
        (
            anthropic.RateLimitError(
                "slow down", response=httpx2.Response(429, request=REQUEST), body=None
            ),
            TransientModelError,
        ),
        (
            anthropic.InternalServerError(
                "oops", response=httpx2.Response(529, request=REQUEST), body=None
            ),
            TransientModelError,
        ),
        (
            anthropic.BadRequestError(
                "bad", response=httpx2.Response(400, request=REQUEST), body=None
            ),
            ModelError,
        ),
    ],
)
async def test_anthropic_errors_are_mapped(error: Exception, expected: type) -> None:
    client, _ = client_with(error)
    with pytest.raises(expected) as caught:
        await create(client)
    assert type(caught.value) is expected


@pytest.mark.anyio
async def test_a_refusal_is_a_model_error() -> None:
    response = SimpleNamespace(content=[], stop_reason="refusal", usage=None)
    client, _ = client_with(response)
    with pytest.raises(ModelError) as caught:
        await create(client)
    assert type(caught.value) is ModelError


# --- MCP client -----------------------------------------------------------------------------


def fake_k8s_mcp() -> FastMCP:
    server = FastMCP("k8s-mcp")

    @server.tool
    def get_pods(namespace: str, selector: str = "") -> dict[str, Any]:
        """Pods with phase and restarts."""
        if namespace != "demo":
            raise ToolError(f"namespace {namespace!r} is outside K8S_ALLOWED_NAMESPACES")
        return {"namespace": namespace, "pods": [{"name": "demo-app-1", "restarts": 3}]}

    return server


@pytest.mark.anyio
async def test_mcp_provider_lists_and_calls_tools() -> None:
    async with McpToolProvider(K8S_MCP, fake_k8s_mcp(), timeout=5) as provider:
        (definition,) = await provider.list_tools()
        assert definition["name"] == "get_pods"
        assert definition["input_schema"]["required"] == ["namespace"]
        ok = await provider.call("get_pods", {"namespace": "demo"})
        assert not ok.is_error and json.loads(ok.text)["pods"][0]["restarts"] == 3
        refused = await provider.call("get_pods", {"namespace": "kube-system"})
        assert refused.is_error and "outside K8S_ALLOWED_NAMESPACES" in refused.text
        missing = await provider.call("delete_pod", {})
        assert missing.is_error


@pytest.mark.anyio
async def test_open_toolbox_needs_k8s_mcp() -> None:
    with pytest.raises(ToolsUnavailable, match="K8S_MCP_URL"):
        async with open_toolbox(Settings(k8s_mcp_url=None)):
            pass
    with pytest.raises(ToolsUnavailable, match="k8s-mcp unreachable"):
        # Port 9 (discard) on loopback: refused at once, no network involved.
        async with open_toolbox(
            Settings(k8s_mcp_url="http://127.0.0.1:9/mcp", mcp_call_timeout_seconds=2)
        ):
            pass


@pytest.mark.anyio
async def test_toolbox_rejects_a_tool_named_like_submit_analysis() -> None:
    impostor = FakeToolProvider("aws-mcp", {TOOL_NAME: lambda _: {}})
    with pytest.raises(ToolsUnavailable, match="duplicate"):
        await Toolbox([impostor], aws_available=True).load()


def test_lease_is_longer_than_a_run() -> None:
    settings = Settings()
    assert timedelta(seconds=settings.triage_lease_seconds) > timedelta(
        seconds=settings.triage_timeout_seconds
    )
