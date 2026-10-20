"""The agent loop's limits and failure paths, on a scripted model and fake tools."""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.model import ModelError, TransientModelError
from app.agents.runner import (
    EVIDENCE_EXCERPT_CHARS,
    SUBMIT_REMINDER,
    AgentRunner,
    RunLimits,
    RunResult,
)
from app.agents.tools import Toolbox
from app.enums import AgentRunOutcome, SubjectType
from tests.unit.agent_fakes import (
    FakeModelClient,
    FakeToolProvider,
    call,
    hang,
    k8s_oom,
    submit,
    turn,
)
from tests.unit.conftest import NOW
from tests.unit.fakes import FakeStore

pytestmark = pytest.mark.anyio


async def run(
    store: FakeStore,
    model: FakeModelClient,
    *,
    limits: RunLimits | None = None,
    provider: FakeToolProvider | None = None,
) -> RunResult:
    runner = AgentRunner(model, store.uow, limits=limits or RunLimits(), clock=lambda: NOW)
    toolbox = await Toolbox([provider or k8s_oom()], aws_available=False).load()
    return await runner.run(
        subject_type=SubjectType.incident,
        subject_id=1,
        model="claude-haiku-5-5",
        task_prompt="Triage incident INC-1.",
        toolbox=toolbox,
    )


async def test_one_transient_error_is_retried(store: FakeStore) -> None:
    model = FakeModelClient([TransientModelError("overloaded"), submit()])
    result = await run(store, model)
    assert result.outcome is AgentRunOutcome.succeeded
    assert len(model.requests) == 2


async def test_a_second_transient_error_fails_the_run(store: FakeStore) -> None:
    pods = turn(call("get_pods", {"namespace": "demo"}))
    model = FakeModelClient([TransientModelError("a"), pods, TransientModelError("b")])
    result = await run(store, model)
    assert result.outcome is AgentRunOutcome.failed
    assert "after a retry" in result.detail
    assert store.agent_runs.rows[0].outcome is AgentRunOutcome.failed


async def test_a_non_transient_error_is_not_retried(store: FakeStore) -> None:
    model = FakeModelClient([ModelError("Anthropic API error 400")])
    result = await run(store, model)
    assert result.outcome is AgentRunOutcome.failed
    assert len(model.requests) == 1


async def test_the_run_times_out(store: FakeStore) -> None:
    result = await run(store, FakeModelClient([hang]), limits=RunLimits(timeout_seconds=0.05))
    assert result.outcome is AgentRunOutcome.timed_out
    (row,) = store.agent_runs.rows
    assert row.outcome is AgentRunOutcome.timed_out and row.finished_at == NOW


async def test_the_token_budget_ends_the_run(store: FakeStore) -> None:
    pods = turn(call("get_pods", {"namespace": "demo"}), input_tokens=900, output_tokens=200)
    result = await run(store, FakeModelClient([pods, pods]), limits=RunLimits(token_budget=2000))
    assert result.outcome is AgentRunOutcome.budget_exceeded
    assert (result.input_tokens, result.output_tokens) == (1800, 400)
    row = store.agent_runs.rows[0]
    assert (row.input_tokens, row.output_tokens) == (1800, 400)


async def test_a_turn_without_tools_gets_one_reminder(store: FakeStore) -> None:
    model = FakeModelClient([turn(text="I think it's OOM."), submit()])
    result = await run(store, model)
    assert result.outcome is AgentRunOutcome.succeeded
    assert model.requests[1]["messages"][-1] == {"role": "user", "content": SUBMIT_REMINDER}


async def test_two_turns_without_submitting_fail_the_run(store: FakeStore) -> None:
    result = await run(store, FakeModelClient([turn(text="hmm"), turn(text="still hmm")]))
    assert result.outcome is AgentRunOutcome.failed
    assert "never called submit_analysis" in result.detail


async def test_parallel_calls_past_the_limit_are_refused(store: FakeStore) -> None:
    calls = [call("get_pods", {"namespace": "demo"}, id=f"t{i}") for i in range(3)]
    model = FakeModelClient([turn(*calls), submit()])
    result = await run(store, model, limits=RunLimits(max_tool_calls=2))
    assert result.outcome is AgentRunOutcome.succeeded
    assert result.tool_calls == 2
    assert [r["is_error"] for r in model.tool_results(1)] == [False, False, True]


async def test_long_results_are_truncated_in_evidence(store: FakeStore) -> None:
    provider = FakeToolProvider("k8s-mcp", {"get_pod_logs": lambda _: "x" * 50_000})
    model = FakeModelClient([turn(call("get_pod_logs", {"pod": "p"})), submit()])
    await run(store, model, provider=provider)
    excerpt = store.evidence.rows[0].result_excerpt
    assert excerpt.startswith("x" * EVIDENCE_EXCERPT_CHARS)
    assert excerpt.endswith("[truncated 42000 characters]")


async def test_an_unexpected_error_still_closes_the_run(store: FakeStore) -> None:
    class Broken(FakeToolProvider):
        async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
            raise RuntimeError("bug")

    provider = Broken("k8s-mcp", {"get_pods": lambda _: {}})
    model = FakeModelClient([turn(call("get_pods"))])
    result = await run(store, model, provider=provider)
    assert result.outcome is AgentRunOutcome.failed
    assert store.agent_runs.rows[0].finished_at == NOW


async def test_conversation_keeps_the_assistant_turns(store: FakeStore) -> None:
    first = turn(call("get_pods", {"namespace": "demo"}, id="t1"), text="Checking pods.")
    model = FakeModelClient([first, submit()])
    await run(store, model)
    messages = model.requests[1]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[1]["content"] == first.content
    assert messages[2]["content"][0]["tool_use_id"] == "t1"
    assert model.requests[0]["model"] == "claude-haiku-5-5"
    assert "untrusted data" in model.requests[0]["system"].lower()
