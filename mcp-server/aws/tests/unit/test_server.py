"""The MCP surface: tool names and arguments, annotations, errors, the probes, and startup."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from starlette.testclient import TestClient

from aws_mcp import __main__ as entry
from aws_mcp.server import build_server
from aws_mcp.settings import Settings

# product-spec.md → Triage agent and guardrails → MCP tools (aws-mcp): names and arguments.
SPEC_TOOLS = {
    "get_resource_changes": ["resource_arn", "hours"],
    "get_principal_activity": ["principal_arn", "hours"],
    "get_iam_role": ["role_name"],
    "simulate_access": ["role_name", "actions", "resources"],
    "get_security_group": ["group_id"],
    "lookup_terraform_address": ["resource_arn"],
}
# Change analysis runs with aws-mcp's tools only: every tool its prompt names must exist here.
CHANGE_PROMPT = (
    Path(__file__).resolve().parents[4] / "backend/app/agents/prompts/change_analysis_v4.md"
)


class FakeAws:
    """An AwsCaller with canned answers."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.error: Exception | None = None
        self.credentials: bool | Exception = True

    def call(self, service: str, operation: str, **params: Any) -> dict[str, Any]:
        self.calls.append((service, operation, params))
        if self.error:
            raise self.error
        if operation == "DescribeSecurityGroups":
            return {
                "SecurityGroups": [
                    {
                        "GroupId": params["GroupIds"][0],
                        "GroupName": "demo-app-sg",
                        "IpPermissions": [
                            {
                                "IpProtocol": "tcp",
                                "FromPort": 22,
                                "ToPort": 22,
                                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                            }
                        ],
                        "IpPermissionsEgress": [],
                    }
                ]
            }
        return {}

    def credentials_available(self) -> bool:
        if isinstance(self.credentials, Exception):
            raise self.credentials
        return self.credentials


def run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture
def aws() -> FakeAws:
    return FakeAws()


@pytest.fixture
def server(settings: Settings, aws: FakeAws):
    return build_server(settings, aws)


def call(server, tool: str, arguments: dict[str, Any]):
    async def go():
        async with Client(server) as client:
            return await client.call_tool(tool, arguments, raise_on_error=False)

    return run(go())


def test_tools_match_the_spec_and_are_read_only(server) -> None:
    async def list_tools():
        async with Client(server) as client:
            return await client.list_tools()

    tools = {t.name: t for t in run(list_tools())}

    assert set(tools) == set(SPEC_TOOLS)
    named = set(re.findall(r"`((?:get|simulate|lookup)_[a-z_]+)`", CHANGE_PROMPT.read_text()))
    assert named and set(tools) >= named
    for name, args in SPEC_TOOLS.items():
        assert list(tools[name].input_schema["properties"]) == args, name
        assert tools[name].annotations.read_only_hint is True
        assert tools[name].annotations.destructive_hint is False
    hours = tools["get_resource_changes"].input_schema["properties"]["hours"]
    assert (hours["minimum"], hours["maximum"], hours["default"]) == (1, 72, 24)


def test_call_through_mcp(server, aws: FakeAws) -> None:
    result = call(server, "get_security_group", {"group_id": "sg-0123456789abcdef0"})

    assert not result.is_error
    assert result.structured_content["inbound"][0]["open_to_world"] is True
    assert aws.calls[0][:2] == ("ec2", "DescribeSecurityGroups")


def test_refusal_reaches_the_model_as_a_tool_error(server, aws: FakeAws) -> None:
    result = call(server, "get_resource_changes", {"resource_arn": "sg-1", "hours": 24})

    assert result.is_error
    assert "not an ARN" in result.content[0].text
    assert aws.calls == []


def test_hours_out_of_range_is_rejected_by_the_schema(server, aws: FakeAws) -> None:
    result = call(
        server,
        "get_resource_changes",
        {"resource_arn": "arn:aws:s3:::bucket", "hours": 500},
    )

    assert result.is_error
    assert aws.calls == []


def test_unexpected_errors_are_masked(server, aws: FakeAws) -> None:
    aws.error = RuntimeError("internal detail AKIAIOSFODNN7EXAMPLE")

    result = call(server, "get_security_group", {"group_id": "sg-0123456789abcdef0"})

    assert result.is_error
    assert "internal detail" not in result.content[0].text


def test_healthz_and_readyz(server, aws: FakeAws) -> None:
    with TestClient(server.http_app(path="/mcp")) as http:
        assert http.get("/healthz").json() == {"status": "ok"}
        assert http.get("/readyz").json() == {"status": "ok", "checks": {"aws": "ok"}}

        aws.credentials = False
        not_ready = http.get("/readyz")
        assert not_ready.status_code == 503
        assert not_ready.json()["checks"] == {"aws": "error"}

        aws.credentials = RuntimeError("container credentials endpoint down")
        assert http.get("/readyz").status_code == 503
    # The probes never call AWS (and never spend LookupEvents' rate limit).
    assert aws.calls == []


def test_fixtures_mode_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_MCP_MODE", "fixtures")
    monkeypatch.setenv("WATCHED_IAM_PATHS", "/demo-app/")

    with pytest.raises(SystemExit) as exited:
        entry.main()

    assert "AWS_MCP_MODE=fixtures isn't implemented yet" in str(exited.value.code)


@pytest.mark.parametrize("transport", ["http", "stdio"])
def test_live_mode_runs_the_chosen_transport(
    monkeypatch: pytest.MonkeyPatch, transport: str
) -> None:
    monkeypatch.setenv("WATCHED_IAM_PATHS", "/demo-app/")
    monkeypatch.setenv("AWS_MCP_TRANSPORT", transport)
    monkeypatch.setenv("AWS_MCP_PORT", "9090")
    runs: list[dict[str, Any]] = []
    monkeypatch.setattr("fastmcp.FastMCP.run", lambda self, **kwargs: runs.append(kwargs))
    monkeypatch.setattr(entry.telemetry, "setup", lambda *args, **kwargs: None)

    entry.main()

    [kwargs] = runs
    assert kwargs["transport"] == transport
    if transport == "http":
        assert (kwargs["port"], kwargs["path"], kwargs["stateless_http"]) == (9090, "/mcp", True)
