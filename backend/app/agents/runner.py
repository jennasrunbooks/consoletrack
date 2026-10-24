"""The agent loop: model → tool calls → evidence → … → `submit_analysis`.

Per run (product-spec.md → Triage agent and guardrails → Runtime):

- at most `triage_max_tool_calls` evidence calls (12). A call past the limit gets a tool error
  telling the model to submit; if its next turn still doesn't, the run ends `limit_reached`;
- `triage_timeout_seconds` (120 s) for the whole run → `timed_out`;
- `triage_token_budget` input plus output tokens → `budget_exceeded`;
- one automatic retry of a transient model error; a second one, or any other error → `failed`.

Every evidence call becomes an `evidence` row (tool, redacted arguments, redacted and truncated
result) as soon as it returns, so a run that stops early still shows what it saw. Results are
redacted before the model sees them too. The run ends only when the model calls
`submit_analysis` with input that validates; there is no free-text parsing.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.agents.model import ModelClient, ModelError, ModelTurn, ToolCall, TransientModelError
from app.agents.prompts import PROMPT_VERSION, load
from app.agents.redact import redact, redact_value
from app.agents.schema import TOOL_NAME, SubmitAnalysis, tool_definition
from app.agents.tools import Toolbox
from app.db.models import AgentRunRow, EvidenceRow
from app.db.repositories import UnitOfWork
from app.enums import AgentRunOutcome, SubjectType
from app.settings import Settings

log = logging.getLogger(__name__)

# What the evidence tab stores per call, and what the model reads back.
EVIDENCE_EXCERPT_CHARS = 8_000
MODEL_RESULT_CHARS = 20_000

SUBMIT_REMINDER = (
    f"You ended your turn without calling {TOOL_NAME}. Call {TOOL_NAME} now with what you "
    "have found; use low confidence and the insufficient_evidence flag if it isn't enough."
)
LIMIT_MESSAGE = (
    f"Tool-call limit reached: this call was not run. Call {TOOL_NAME} now with the evidence "
    "you already have."
)


@dataclass(frozen=True)
class RunLimits:
    max_tool_calls: int = 12
    timeout_seconds: float = 120
    token_budget: int = 200_000
    max_output_tokens: int = 8_192
    transient_retries: int = 1

    @classmethod
    def from_settings(cls, settings: Settings) -> RunLimits:
        return cls(
            max_tool_calls=settings.triage_max_tool_calls,
            timeout_seconds=settings.triage_timeout_seconds,
            token_budget=settings.triage_token_budget,
            max_output_tokens=settings.triage_max_output_tokens,
        )


@dataclass(frozen=True)
class RunResult:
    run_id: int
    outcome: AgentRunOutcome
    analysis: SubmitAnalysis | None
    # Why the run stopped, for the incident's record when there's no analysis.
    detail: str
    tool_calls: int
    input_tokens: int
    output_tokens: int
    # The evidence calls that succeeded, as (tool name, redacted result), in call order.
    evidence: tuple[tuple[str, str], ...] = ()


class _Stop(Exception):
    def __init__(self, outcome: AgentRunOutcome, detail: str) -> None:
        self.outcome = outcome
        self.detail = detail


class AgentRunner:
    def __init__(
        self,
        model_client: ModelClient,
        uow_factory: Callable[[], UnitOfWork],
        *,
        limits: RunLimits,
        clock: Callable[[], datetime],
    ) -> None:
        self._model = model_client
        self._uow_factory = uow_factory
        self._limits = limits
        self._clock = clock

    async def run(
        self,
        *,
        subject_type: SubjectType,
        subject_id: int,
        model: str,
        task_prompt: str,
        toolbox: Toolbox,
        escalated: bool = False,
    ) -> RunResult:
        run = _Run(self, subject_type, subject_id, model, toolbox, escalated)
        try:
            async with asyncio.timeout(self._limits.timeout_seconds):
                analysis = await run.loop(task_prompt)
            outcome, detail = AgentRunOutcome.succeeded, "analysis submitted"
        except TimeoutError:
            analysis = None
            outcome = AgentRunOutcome.timed_out
            detail = f"the run took longer than {self._limits.timeout_seconds:g} s"
        except _Stop as stop:
            analysis, outcome, detail = None, stop.outcome, stop.detail
        except Exception:
            # A bug or an unexpected tool failure must still close the run row.
            log.exception("agent run %s failed", run.run_id)
            analysis, outcome, detail = None, AgentRunOutcome.failed, "internal error"
        run.finish(outcome)
        log.info(
            "agent run finished run=%s subject=%s/%s outcome=%s tool_calls=%d tokens=%d/%d",
            run.run_id,
            subject_type.value,
            subject_id,
            outcome.value,
            run.tool_calls,
            run.input_tokens,
            run.output_tokens,
        )
        return RunResult(
            run_id=run.run_id,
            outcome=outcome,
            analysis=analysis,
            detail=detail,
            tool_calls=run.tool_calls,
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
            evidence=tuple(run.evidence),
        )

    def failed_start(
        self, *, subject_type: SubjectType, subject_id: int, model: str, detail: str
    ) -> RunResult:
        """Record a run that couldn't start (no tools), so the subject shows why."""
        now = self._clock()
        with self._uow_factory() as uow:
            row = uow.agent_runs.add(
                AgentRunRow(
                    subject_type=subject_type,
                    subject_id=subject_id,
                    model=model,
                    prompt_version=PROMPT_VERSION,
                    started_at=now,
                    finished_at=now,
                    input_tokens=0,
                    output_tokens=0,
                    outcome=AgentRunOutcome.failed,
                )
            )
            uow.commit()
            run_id = row.id
        return RunResult(
            run_id=run_id,
            outcome=AgentRunOutcome.failed,
            analysis=None,
            detail=detail,
            tool_calls=0,
            input_tokens=0,
            output_tokens=0,
        )


class _Run:
    """One run's state. Each database write is its own short unit of work."""

    def __init__(
        self,
        runner: AgentRunner,
        subject_type: SubjectType,
        subject_id: int,
        model: str,
        toolbox: Toolbox,
        escalated: bool,
    ) -> None:
        self.runner = runner
        self.limits = runner._limits
        self.model = model
        self.toolbox = toolbox
        self.tool_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.evidence: list[tuple[str, str]] = []
        with runner._uow_factory() as uow:
            row = uow.agent_runs.add(
                AgentRunRow(
                    subject_type=subject_type,
                    subject_id=subject_id,
                    model=model,
                    prompt_version=PROMPT_VERSION,
                    escalated=escalated,
                    started_at=runner._clock(),
                    outcome=AgentRunOutcome.running,
                )
            )
            uow.commit()
            self.run_id: int = row.id

    async def loop(self, task_prompt: str) -> SubmitAnalysis:
        system = load("system")
        tools = [*self.toolbox.definitions, tool_definition()]
        messages: list[dict[str, Any]] = [{"role": "user", "content": task_prompt}]
        retries = self.limits.transient_retries
        reminded = False
        over_limit = False
        while True:
            if self.input_tokens + self.output_tokens >= self.limits.token_budget:
                raise _Stop(
                    AgentRunOutcome.budget_exceeded,
                    f"the run used its token budget ({self.limits.token_budget})",
                )
            try:
                turn = await self.runner._model.create(
                    model=self.model,
                    system=system,
                    messages=messages,
                    tools=tools,
                    max_tokens=self.limits.max_output_tokens,
                )
            except TransientModelError as exc:
                if retries > 0:
                    retries -= 1
                    log.warning("transient model error, retrying once: %s", exc)
                    continue
                raise _Stop(AgentRunOutcome.failed, f"model error after a retry: {exc}") from exc
            except ModelError as exc:
                raise _Stop(AgentRunOutcome.failed, f"model error: {exc}") from exc
            self._count(turn)
            messages.append({"role": "assistant", "content": turn.content})

            if not turn.tool_calls:
                if reminded:
                    raise _Stop(AgentRunOutcome.failed, f"the model never called {TOOL_NAME}")
                reminded = True
                messages.append({"role": "user", "content": SUBMIT_REMINDER})
                continue

            if over_limit and not any(call.name == TOOL_NAME for call in turn.tool_calls):
                raise _Stop(
                    AgentRunOutcome.limit_reached,
                    f"the run used its {self.limits.max_tool_calls} tool calls without "
                    f"submitting an analysis",
                )
            results: list[dict[str, Any]] = []
            submitted: SubmitAnalysis | None = None
            for call in turn.tool_calls:
                if call.name == TOOL_NAME:
                    submitted, result = self._submit(call)
                    results.append(result)
                elif self.tool_calls >= self.limits.max_tool_calls:
                    over_limit = True
                    results.append(_tool_result(call.id, LIMIT_MESSAGE, is_error=True))
                else:
                    results.append(await self._evidence(call))
            if submitted is not None:
                return submitted
            messages.append({"role": "user", "content": results})

    def _count(self, turn: ModelTurn) -> None:
        self.input_tokens += turn.input_tokens
        self.output_tokens += turn.output_tokens

    def _submit(self, call: ToolCall) -> tuple[SubmitAnalysis | None, dict[str, Any]]:
        try:
            analysis = SubmitAnalysis.model_validate(call.input)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in error['loc']) or 'input'}: {error['msg']}"
                for error in exc.errors()[:10]
            )
            log.info("run %s: submit_analysis rejected: %s", self.run_id, problems)
            return None, _tool_result(
                call.id, f"{TOOL_NAME} input is invalid, fix it and call again: {problems}", True
            )
        return analysis, _tool_result(call.id, "Analysis recorded.", is_error=False)

    async def _evidence(self, call: ToolCall) -> dict[str, Any]:
        self.tool_calls += 1
        result = await self.toolbox.call(call.name, call.input)
        text = redact(result.text)
        with self.runner._uow_factory() as uow:
            uow.evidence.add(
                EvidenceRow(
                    agent_run_id=self.run_id,
                    seq=self.tool_calls,
                    tool_name=call.name,
                    args_json=redact_value(call.input),
                    result_excerpt=_truncate(text, EVIDENCE_EXCERPT_CHARS),
                )
            )
            uow.commit()
        if not result.is_error:
            self.evidence.append((call.name, text))
        return _tool_result(call.id, _truncate(text, MODEL_RESULT_CHARS), result.is_error)

    def finish(self, outcome: AgentRunOutcome) -> None:
        with self.runner._uow_factory() as uow:
            row = uow.agent_runs.get(self.run_id)
            if row is None:  # the FK target was just created; unreachable in Postgres
                return
            row.outcome = outcome
            row.finished_at = self.runner._clock()
            row.input_tokens = self.input_tokens
            row.output_tokens = self.output_tokens
            uow.commit()


def _tool_result(tool_use_id: str, content: str, is_error: bool) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
        "is_error": is_error,
    }


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n… [truncated {len(text) - limit} characters]"
