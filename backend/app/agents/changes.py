"""Change analysis: the agent's write-up of one out-of-band AWS change (product-spec.md → Change
flow, step 3), which moves it `detected → awaiting_review`.

The intake (#9) calls `ChangeAnalysis.analyze(change_id)` after it records a new change; that
replaces its no-op. The agent reads through `aws-mcp` when it's configured and otherwise works
from the stored CloudTrail record. It never proposes a fix for a change: the reviewer picks
Revert, Codify, or Accept, so a proposal in the result is dropped.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.agents.prompts import render
from app.agents.runner import AgentRunner, RunResult
from app.agents.tools import ToolsUnavailable
from app.agents.triage import AWS_AVAILABLE, AWS_UNAVAILABLE, ToolboxFactory
from app.db.models import ChangeRow
from app.db.repositories import UnitOfWork
from app.enums import SubjectType
from app.services.changes import record_change_analysis
from app.services.errors import NotFound
from app.settings import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChangeBrief:
    """What the prompt reads from a change, copied while its session is open.

    A unit of work that exits without a commit rolls back, which expires every loaded row, so
    the ORM row can't be read after the `with` block.
    """

    change_id: int
    record: dict[str, Any]


def change_brief(row: ChangeRow) -> ChangeBrief:
    return ChangeBrief(
        change_id=row.id,
        record={
            "cloudtrail_event_id": row.cloudtrail_event_id,
            "event_time": row.event_time.isoformat(),
            "event_name": row.event_name,
            "service": row.service,
            "region": row.region,
            "resource_arn": row.resource_arn,
            "resource_name": row.resource_name,
            "tf_address": row.tf_address,
            "principal_arn": row.principal_arn,
            "session_name": row.session_name,
            "via": row.via.value,
            "source_ip": row.source_ip,
            "user_agent": row.user_agent,
            "request_parameters": row.request_parameters,
            "drift": row.drift,
            "intake_risk": row.risk.value if row.risk else None,
        },
    )


def change_prompt(brief: ChangeBrief, *, aws_available: bool) -> str:
    return render(
        "change_analysis",
        change_id=brief.change_id,
        change_json=json.dumps(brief.record, indent=2, sort_keys=True, default=str),
        aws_note=AWS_AVAILABLE if aws_available else AWS_UNAVAILABLE,
    )


class ChangeAnalysis:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        runner: AgentRunner,
        settings: Settings,
        toolbox_factory: ToolboxFactory,
        clock: Callable[[], datetime],
    ) -> None:
        self._uow_factory = uow_factory
        self._runner = runner
        self._settings = settings
        self._toolbox_factory = toolbox_factory
        self._clock = clock

    async def analyze(self, change_id: int) -> RunResult:
        with self._uow_factory() as uow:
            row = uow.changes.get(change_id)
            if row is None:
                raise NotFound("change", change_id)
            brief = change_brief(row)
        model = self._settings.triage_model
        started = False
        try:
            async with self._toolbox_factory(self._settings) as toolbox:
                prompt = change_prompt(brief, aws_available=toolbox.aws_available)
                started = True
                result = await self._runner.run(
                    subject_type=SubjectType.change,
                    subject_id=change_id,
                    model=model,
                    task_prompt=prompt,
                    toolbox=toolbox,
                )
        except ToolsUnavailable as exc:
            result = self._failed_start(change_id, model, f"tools unavailable: {exc}")
        except Exception as exc:
            if started:
                raise
            # Record a failed run so `max_runs` stops the retries and the change shows why. The
            # class name only: the message could hold request data.
            log.exception("change %s: analysis could not start", change_id)
            result = self._failed_start(
                change_id, model, f"analysis could not start: {type(exc).__name__}"
            )
        if result.analysis is not None and result.analysis.proposed_remediation is not None:
            log.info("change %s: dropped a proposal; changes are reviewed by hand", change_id)
        with self._uow_factory() as uow:
            record_change_analysis(
                uow,
                change_id,
                run_id=result.run_id,
                analysis=result.analysis,
                stopped=result.detail,
                now=self._clock(),
            )
        return result

    def _failed_start(self, change_id: int, model: str, detail: str) -> RunResult:
        return self._runner.failed_start(
            subject_type=SubjectType.change, subject_id=change_id, model=model, detail=detail
        )
