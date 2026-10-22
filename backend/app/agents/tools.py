"""The agent's tools: read-only MCP servers behind a `ToolProvider` protocol.

`k8s-mcp` is required for incident triage. `aws-mcp` is optional: with `AWS_MCP_URL` empty
(kind, Compose) the run offers only the cluster tools and the task prompt says AWS evidence
isn't available. That is a normal configuration, not an error, so nothing retries it. A
configured `aws-mcp` that can't be reached is logged and treated the same way.

Unit tests use in-process fakes with the same tool names (`tests/unit/agent_fakes.py`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from fastmcp import Client
from opentelemetry.trace import SpanKind

from app.agents.schema import TOOL_NAME
from app.settings import Settings
from app.telemetry import tracer

log = logging.getLogger(__name__)

K8S_MCP = "k8s-mcp"
AWS_MCP = "aws-mcp"


@dataclass(frozen=True)
class ToolResult:
    text: str
    is_error: bool = False


class ToolProvider(Protocol):
    name: str

    async def list_tools(self) -> list[dict[str, Any]]:
        """Tool definitions as the Messages API takes them: name, description, input_schema."""
        ...

    async def call(self, tool: str, arguments: dict[str, Any]) -> ToolResult: ...


class ToolsUnavailable(Exception):
    """A required MCP server isn't configured or can't be reached."""


class McpToolProvider:
    """One MCP server over streamable HTTP. Use as an async context manager.

    `transport` is the server's URL; tests pass an in-process FastMCP server instead.
    """

    def __init__(self, name: str, transport: Any, *, timeout: float) -> None:
        self.name = name
        self._client = Client(transport, timeout=timeout, init_timeout=timeout)
        self._timeout = timeout

    async def __aenter__(self) -> McpToolProvider:
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.__aexit__(*exc)

    async def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.input_schema,
            }
            for tool in await self._client.list_tools()
        ]

    async def call(self, tool: str, arguments: dict[str, Any]) -> ToolResult:
        try:
            result = await self._client.call_tool(
                tool, arguments, raise_on_error=False, timeout=self._timeout
            )
        except Exception as exc:  # transport errors, timeouts: the model sees a tool error
            log.warning("tool call failed server=%s tool=%s error=%s", self.name, tool, exc)
            return ToolResult(f"{self.name} call failed: {type(exc).__name__}", is_error=True)
        if result.structured_content is not None:
            text = json.dumps(result.structured_content, default=str)
        else:
            text = "\n".join(getattr(block, "text", "") for block in result.content)
        return ToolResult(text, is_error=result.is_error)


class Toolbox:
    """The tools of every connected provider, by name, plus `aws_available` for the prompt."""

    def __init__(self, providers: Sequence[ToolProvider], *, aws_available: bool) -> None:
        self.providers = list(providers)
        self.aws_available = aws_available
        self._owner: dict[str, ToolProvider] = {}
        self._definitions: list[dict[str, Any]] = []
        # Evidence calls made through this toolbox, over every run that shares it.
        self.calls = 0

    async def load(self) -> Toolbox:
        for provider in self.providers:
            for definition in await provider.list_tools():
                name = definition["name"]
                if name == TOOL_NAME or name in self._owner:
                    raise ToolsUnavailable(f"{provider.name} offers a duplicate tool name {name!r}")
                self._owner[name] = provider
                self._definitions.append(definition)
        return self

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return list(self._definitions)

    async def call(self, tool: str, arguments: dict[str, Any]) -> ToolResult:
        provider = self._owner.get(tool)
        if provider is None:
            return ToolResult(f"unknown tool {tool!r}", is_error=True)
        self.calls += 1
        # The tool's name and the result's size only: arguments and results are evidence, and
        # never reach telemetry. FastMCP's client span below this one carries the traceparent
        # to the server in the request's _meta.
        with tracer.start_as_current_span(
            "mcp.call",
            kind=SpanKind.CLIENT,
            attributes={"mcp.server": provider.name, "mcp.tool": tool},
        ) as span:
            result = await provider.call(tool, arguments)
            span.set_attributes(
                {"mcp.result_bytes": len(result.text.encode()), "mcp.error": result.is_error}
            )
            return result


@asynccontextmanager
async def open_toolbox(settings: Settings) -> AsyncIterator[Toolbox]:
    """Incident triage: k8s-mcp (required) and aws-mcp (optional) for one run."""
    if not settings.k8s_mcp_url:
        raise ToolsUnavailable("K8S_MCP_URL is not set")
    async with _toolbox(settings, include_k8s=True) as toolbox:
        yield toolbox


@asynccontextmanager
async def open_aws_toolbox(settings: Settings) -> AsyncIterator[Toolbox]:
    """Change analysis: aws-mcp only, and optional. Without it the agent works from the
    change record alone."""
    async with _toolbox(settings, include_k8s=False) as toolbox:
        yield toolbox


@asynccontextmanager
async def _toolbox(settings: Settings, *, include_k8s: bool) -> AsyncIterator[Toolbox]:
    timeout = settings.mcp_call_timeout_seconds
    async with AsyncExitStack() as stack:
        providers: list[ToolProvider] = []
        if include_k8s:
            try:
                providers.append(
                    await stack.enter_async_context(
                        McpToolProvider(K8S_MCP, settings.k8s_mcp_url, timeout=timeout)
                    )
                )
            except Exception as exc:
                raise ToolsUnavailable(f"{K8S_MCP} unreachable: {type(exc).__name__}") from exc
        if settings.aws_mcp_url:
            try:
                providers.append(
                    await stack.enter_async_context(
                        McpToolProvider(AWS_MCP, settings.aws_mcp_url, timeout=timeout)
                    )
                )
            except Exception as exc:
                log.warning("aws-mcp unreachable, continuing without AWS tools: %s", exc)
        toolbox = Toolbox(providers, aws_available=any(p.name == AWS_MCP for p in providers))
        yield await toolbox.load()
