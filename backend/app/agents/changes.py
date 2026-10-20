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
from datetime import datetime

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


def change_prompt(row: ChangeRow, *, aws_available: bool) -> str:
    record = {
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
    }
    return render(
        "change_analysis",
        change_id=row.id,
        change_json=json.dumps(record, indent=2, sort_keys=True, default=str),
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
        model = self._settings.triage_model
        try:
            async with self._toolbox_factory(self._settings) as toolbox:
                result = await self._runner.run(
                    subject_type=SubjectType.change,
                    subject_id=change_id,
                    model=model,
                    task_prompt=change_prompt(row, aws_available=toolbox.aws_available),
                    toolbox=toolbox,
                )
        except ToolsUnavailable as exc:
            result = self._runner.failed_start(
                subject_type=SubjectType.change,
                subject_id=change_id,
                model=model,
                detail=f"tools unavailable: {exc}",
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
