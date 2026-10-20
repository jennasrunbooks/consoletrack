"""The model behind the agent loop: a `ModelClient` protocol, the Anthropic implementation, and
the errors the runner tells apart (one transient retry per run; anything else ends the run).

Tests and CI use `tests/unit/agent_fakes.py`'s `FakeModelClient`, which replays a script; no test
calls the real API or needs `ANTHROPIC_API_KEY`. Model IDs come from settings only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ModelTurn:
    """One model response. `content` goes back into the conversation unchanged (it can hold
    thinking blocks, which the API needs to see again on the next request)."""

    content: list[Any]
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class ModelError(Exception):
    """The request failed and retrying won't help (bad request, auth, refusal)."""


class TransientModelError(ModelError):
    """Connection error, timeout, rate limit, overload, or 5xx: worth one retry."""


class ModelClient(Protocol):
    async def create(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> ModelTurn: ...


# Status codes the Anthropic SDK itself would retry.
_TRANSIENT_STATUS = frozenset({408, 409, 429})


class AnthropicClient:
    """The Messages API with tool use. The SDK's own retries are off: the runner owns the
    per-run retry budget. The API key comes from the environment (`ANTHROPIC_API_KEY`)."""

    def __init__(
        self, client: anthropic.AsyncAnthropic | None = None, *, timeout: float = 60
    ) -> None:
        self._client = client or anthropic.AsyncAnthropic(max_retries=0, timeout=timeout)

    async def create(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> ModelTurn:
        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                tools=tools,
                # `auto`, not a forced tool: current models reject forced tool_choice. The system
                # prompt and the runner's reminder make the model finish with submit_analysis.
                tool_choice={"type": "auto"},
                # The tools and system prompt are the same for every call of a run.
                cache_control={"type": "ephemeral"},
            )
        except anthropic.APIConnectionError as exc:  # includes APITimeoutError
            raise TransientModelError(f"Anthropic API unreachable: {type(exc).__name__}") from exc
        except anthropic.APIStatusError as exc:
            message = f"Anthropic API error {exc.status_code}"
            if exc.status_code >= 500 or exc.status_code in _TRANSIENT_STATUS:
                raise TransientModelError(message) from exc
            raise ModelError(message) from exc
        if response.stop_reason == "refusal":
            raise ModelError("the model declined the request (stop_reason=refusal)")
        return ModelTurn(
            content=list(response.content),
            tool_calls=[
                ToolCall(id=block.id, name=block.name, input=dict(block.input))
                for block in response.content
                if block.type == "tool_use"
            ],
            stop_reason=response.stop_reason,
            input_tokens=_input_tokens(response.usage),
            output_tokens=response.usage.output_tokens,
        )


def _input_tokens(usage: Any) -> int:
    # Cached prefix tokens are input too, for the run's budget and the agent_runs row.
    return (
        (usage.input_tokens or 0)
        + (getattr(usage, "cache_creation_input_tokens", None) or 0)
        + (getattr(usage, "cache_read_input_tokens", None) or 0)
    )
