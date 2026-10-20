"""Incident triage, end to end: claim an incident, run the agent against the MCP tools, record
the result, and hand any proposal to the policy gate.

    Alertmanager → POST /alerts/webhook → incident `open`, queued
    worker: claim → `triaging` → AgentRunner (k8s-mcp, optional aws-mcp, submit_analysis)
          → analysis, links, `proposed` remediation → `awaiting_review` → PolicyGate

The agent never applies anything: the proposal stops at the gate, which in Phase 1 always
answers `manual`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta

from app.agents.prompts import render
from app.agents.runner import AgentRunner, RunResult
from app.agents.schema import SubmitAnalysis
from app.agents.tools import Toolbox, ToolsUnavailable
from app.db.models import AgentRunRow
from app.db.repositories import UnitOfWork
from app.enums import AgentRunOutcome, SubjectType
from app.services import triage
from app.services.policy_gate import GateDecision, PolicyGate
from app.services.triage import TriageClaim
from app.settings import Settings

log = logging.getLogger(__name__)

ToolboxFactory = Callable[[Settings], AbstractAsyncContextManager[Toolbox]]

AWS_AVAILABLE = "AWS tools (CloudTrail, IAM, security groups, Terraform state) are available."
AWS_UNAVAILABLE = (
    "AWS tools are not available in this run, so there is no CloudTrail or IAM evidence. If "
    "the cause looks AWS-side, say which AWS evidence would confirm it and add the "
    "aws_evidence_unavailable flag."
)


def incident_prompt(claim: TriageClaim, *, aws_available: bool) -> str:
    alert = {"alertname": claim.alertname, "labels": claim.labels, "annotations": claim.annotations}
    return render(
        "incident_triage",
        incident_id=claim.incident_id,
        alert_json=json.dumps(alert, indent=2, sort_keys=True),
        namespace=claim.namespace or "unknown (no namespace label)",
        workload=claim.workload or "unknown (no workload label)",
        opened_at=claim.opened_at.isoformat(),
        aws_note=AWS_AVAILABLE if aws_available else AWS_UNAVAILABLE,
    )


class IncidentTriage:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        runner: AgentRunner,
        gate: PolicyGate,
        settings: Settings,
        toolbox_factory: ToolboxFactory,
        clock: Callable[[], datetime],
    ) -> None:
        self._uow_factory = uow_factory
        self._runner = runner
        self._gate = gate
        self._settings = settings
        self._toolbox_factory = toolbox_factory
        self._clock = clock

    async def run_next(self) -> bool:
        """Triage the next due incident, if any. Returns whether there was one."""
        with self._uow_factory() as uow:
            claim = triage.claim_next(
                uow,
                now=self._clock(),
                lease=timedelta(seconds=self._settings.triage_lease_seconds),
            )
        if claim is None:
            return False
        await self.triage(claim)
        return True

    async def triage(self, claim: TriageClaim) -> GateDecision | None:
        model = claim.model or self._settings.triage_model
        log.info("triage started incident=%s model=%s", claim.incident_id, model)
        try:
            async with self._toolbox_factory(self._settings) as toolbox:
                result = await self._runner.run(
                    subject_type=SubjectType.incident,
                    subject_id=claim.incident_id,
                    model=model,
                    task_prompt=incident_prompt(claim, aws_available=toolbox.aws_available),
                    toolbox=toolbox,
                )
        except ToolsUnavailable as exc:
            log.error("triage incident=%s: tools unavailable: %s", claim.incident_id, exc)
            result = self._failed_run(claim.incident_id, model, f"tools unavailable: {exc}")
        with self._uow_factory() as uow:
            remediation = triage.record_triage(
                uow,
                claim.incident_id,
                run_id=result.run_id,
                analysis=result.analysis,
                stopped=result.detail,
                now=self._clock(),
            )
        if remediation is None or result.analysis is None:
            return None
        return self._submit(claim.incident_id, result.analysis)

    def _submit(self, incident_id: int, analysis: SubmitAnalysis) -> GateDecision | None:
        proposal = analysis.proposed_remediation
        if proposal is None:
            return None
        decision = self._gate.submit_proposal(incident_id, proposal)
        log.info(
            "proposal incident=%s kind=%s gate=%s policy=%s",
            incident_id,
            proposal.kind,
            decision.mode.value,
            decision.policy,
        )
        return decision

    def _failed_run(self, incident_id: int, model: str, detail: str) -> RunResult:
        """A run row for a run that couldn't start, so the incident shows why."""
        now = self._clock()
        with self._uow_factory() as uow:
            row = uow.agent_runs.add(
                AgentRunRow(
                    subject_type=SubjectType.incident,
                    subject_id=incident_id,
                    model=model,
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
