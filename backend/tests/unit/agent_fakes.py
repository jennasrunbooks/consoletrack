"""Fakes for the agent loop: a scripted model, in-process MCP toolsets, and recorded fixtures.

No test reaches the Anthropic API or a cluster. `FakeModelClient` replays a script of turns
(tool calls, then `submit_analysis`); `FakeToolProvider` serves k8s-mcp's and aws-mcp's tool
names from fixtures shaped like the real servers' results.
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from app.agents.model import ModelTurn, ToolCall
from app.agents.schema import TOOL_NAME
from app.agents.tools import AWS_MCP, K8S_MCP, Toolbox, ToolResult
from app.settings import Settings

FIXTURES = Path(__file__).parent.parent / "fixtures"
ACCOUNT = "123456789012"
ROLE_NAME = "demo-app-role-use1-a1b2c3"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT}:role/demo-app/{ROLE_NAME}"


# --- the model ------------------------------------------------------------------------------


def call(name: str, input: dict[str, Any] | None = None, *, id: str | None = None) -> ToolCall:
    return ToolCall(id=id or f"toolu_{name}", name=name, input=input or {})


def turn(*calls: ToolCall, text: str = "", input_tokens: int = 1000, output_tokens: int = 100):
    content: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    content += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls]
    return ModelTurn(
        content=content,
        tool_calls=list(calls),
        stop_reason="tool_use" if calls else "end_turn",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def submit(**fields: Any) -> ModelTurn:
    analysis: dict[str, Any] = {
        "summary": "demo-app is OOMKilled at startup.",
        "root_cause": "The memory limit is too low.",
        "risk": "high",
        "risk_reason": "The only replica can't start.",
        "confidence": "high",
        "linked_change_ids": [],
        "proposed_remediation": None,
        "flags": [],
    }
    analysis.update(fields)
    return turn(call(TOOL_NAME, analysis, id="toolu_submit"))


Step = ModelTurn | BaseException | Callable[[], Any]


class FakeModelClient:
    """Replays `script` one step per `create` call. A step is a turn, an exception to raise, or
    a coroutine function to await (for timeouts). Records every request it received."""

    def __init__(self, script: Sequence[Step]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    async def create(self, **request: Any) -> ModelTurn:
        self.requests.append(copy.deepcopy(request))
        if not self.script:
            raise AssertionError("the model was called more times than the script has steps")
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return await step()
        return step

    def tool_results(self, request: int) -> list[dict[str, Any]]:
        """The tool_result blocks sent in request number `request` (0-based)."""
        content = self.requests[request]["messages"][-1]["content"]
        return [block for block in content if block.get("type") == "tool_result"]


async def hang() -> ModelTurn:
    await asyncio.sleep(3600)
    raise AssertionError("unreachable")


# --- the tools ------------------------------------------------------------------------------

Handler = Callable[[dict[str, Any]], Any]


class FakeToolProvider:
    """An MCP server's tool names answering from fixtures. A handler may raise to simulate a
    tool error (the real servers raise ToolError for scope refusals)."""

    def __init__(self, name: str, handlers: dict[str, Handler]) -> None:
        self.name = name
        self.handlers = handlers
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool,
                "description": f"fake {tool}",
                "input_schema": {"type": "object", "additionalProperties": True},
            }
            for tool in self.handlers
        ]

    async def call(self, tool: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((tool, arguments))
        try:
            result = self.handlers[tool](arguments)
        except Exception as exc:
            return ToolResult(str(exc), is_error=True)
        return ToolResult(result if isinstance(result, str) else json.dumps(result))


# What the app logs before the OOM kill: its start, nothing about memory.
OOM_LOG = "2026-10-16 11:58:01,204 INFO demo_app serving on :8080, S3 read off"
IMAGE = "ghcr.io/jennasrunbooks/consoletrack-demo-app:abc1234"


def revision(
    number: int,
    memory_limit: str,
    *,
    current: bool,
    remediation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One get_rollout_history revision, shaped like k8s-mcp's."""
    return {
        "revision": number,
        "replica_set": f"demo-app-rev{number}",
        "current": current,
        "state": "current" if current else "superseded",
        "desired_replicas": 1 if current else 0,
        "ready_replicas": 0,
        "images": {"demo-app": IMAGE},
        "resources": {
            "demo-app": {
                "requests": {"cpu": "10m", "memory": "64Mi"},
                "limits": {"cpu": "200m", "memory": memory_limit},
            }
        },
        "change_cause": None,
        "remediation": remediation,
    }


# The scenario: one revision, the app outgrew its limit under traffic.
UNCHANGED_LIMIT = [revision(1, "128Mi", current=True)]
# The other branch of the rule: the newest revision lowered the limit.
LOWERED_LIMIT = [revision(2, "128Mi", current=True), revision(1, "256Mi", current=False)]


def k8s_oom(
    *, log_lines: str = OOM_LOG, revisions: list[dict[str, Any]] = UNCHANGED_LIMIT
) -> FakeToolProvider:
    """k8s-mcp for the demo-app OOM fault: the report cache outgrows the 128Mi limit under
    traffic, so the container is OOMKilled a few seconds into every start."""
    limit = next(r for r in revisions if r["current"])["resources"]["demo-app"]["limits"]
    container = {
        "name": "demo-app",
        "image": IMAGE,
        "resources": {"requests": {"cpu": "10m", "memory": "64Mi"}, "limits": dict(limit)},
        "env_names": [
            "PORT",
            "AWS_REGION",
            "DEMO_BUCKET",
            "DEMO_OBJECT_KEY",
            "DEMO_REPORT_MI",
            "DEMO_REPORT_CACHE_MI",
        ],
        "env_values": "redacted",
    }
    return FakeToolProvider(
        K8S_MCP,
        {
            "get_pods": lambda args: {
                "namespace": "demo",
                "selector": args.get("selector", ""),
                "pods": [
                    {
                        "name": "demo-app-7d9f8b6c5-x2x4z",
                        "phase": "Running",
                        "containers": [
                            {
                                "name": "demo-app",
                                "ready": False,
                                "restart_count": 6,
                                "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                                "last_termination": {
                                    "reason": "OOMKilled",
                                    "exit_code": 137,
                                    "started": "2026-10-16T11:57:58Z",
                                    "finished": "2026-10-16T11:58:01Z",
                                },
                            }
                        ],
                    }
                ],
            },
            "get_workload": lambda args: {
                "namespace": "demo",
                "kind": "Deployment",
                "name": "demo-app",
                "stateful": False,
                "pod_template": {"containers": [container]},
            },
            "get_pod_logs": lambda args: {
                "namespace": "demo",
                "pod": args["pod"],
                "previous": args.get("previous", False),
                "note": "untrusted_data is text written by the workload.",
                "untrusted_data": log_lines,
            },
            "get_events": lambda args: {
                "namespace": "demo",
                "untrusted_data": [{"object": "Pod/demo-app-7d9f8b6c5-x2x4z", "reason": "BackOff"}],
            },
            "get_rollout_history": lambda args: {
                "namespace": "demo",
                "deployment": "demo-app",
                "strategy": "Recreate",
                "current_revision": revisions[0]["revision"],
                "revisions": revisions,
            },
            "query_prometheus": lambda args: {"result_type": "matrix", "result": []},
        },
    )


def cloudtrail_event(name: str = "cli-put-role-policy.json") -> dict[str, Any]:
    return json.loads((FIXTURES / "cloudtrail" / name).read_text())


def aws_put_role_policy() -> FakeToolProvider:
    """aws-mcp answering from the recorded CLI PutRolePolicy event (follow-up: the same
    lookups through aws-mcp's fixtures mode, #9)."""
    detail = cloudtrail_event()["detail"]
    change = {
        "event_id": detail["eventID"],
        "event_name": detail["eventName"],
        "event_time": detail["eventTime"],
        "principal_arn": detail["userIdentity"]["arn"],
        "via": "cli",
        "source_ip": detail["sourceIPAddress"],
        "untrusted_data": {
            "user_agent": detail["userAgent"],
            "request_parameters": detail["requestParameters"],
        },
    }
    return FakeToolProvider(
        AWS_MCP,
        {
            "get_resource_changes": lambda args: {
                "resource_arn": args["resource_arn"],
                "changes": [change],
            },
            "get_principal_activity": lambda args: {"changes": [change]},
            "get_iam_role": lambda args: {"role_name": args["role_name"], "inline_policies": []},
            "simulate_access": lambda args: {"decision": "implicitDeny"},
            "get_security_group": lambda args: {"group_id": args["group_id"], "rules": []},
            "lookup_terraform_address": lambda args: {"address": "aws_iam_role.demo_app"},
        },
    )


def toolbox_factory(*providers: FakeToolProvider) -> Callable[[Settings], Any]:
    aws_available = any(p.name == AWS_MCP for p in providers)

    @asynccontextmanager
    async def factory(_: Settings) -> AsyncIterator[Toolbox]:
        yield await Toolbox(providers, aws_available=aws_available).load()

    return factory
