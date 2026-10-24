"""Incident triage, end to end: claim an incident, run the agent against the MCP tools, record
the result, and hand any proposal to the policy gate.

    Alertmanager → POST /alerts/webhook → incident `open`, queued
    worker: claim → `triaging` → AgentRunner (k8s-mcp, optional aws-mcp, submit_analysis)
          → analysis, links, `proposed` remediation → `awaiting_review` → PolicyGate

The agent never applies anything: the proposal stops at the gate, which in Phase 1 always
answers `manual`.

One escalation (#60): when a run ends with low confidence or no proposal while its evidence shows
a container restart, the worker re-runs once with `TRIAGE_MODEL_ESCALATION`, with its own
tool-call and token budget. Both runs are `agent_runs` rows; the escalated run's analysis is the
incident's (its links and proposal count), unless it stopped without one. The first run's
analysis is kept too, as an earlier row with no proposal. At most one escalation per incident.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta

from app import telemetry
from app.agents.prompts import render
from app.agents.runner import AgentRunner, RunResult
from app.agents.schema import SubmitAnalysis
from app.agents.tools import Toolbox, ToolsUnavailable
from app.db.repositories import UnitOfWork
from app.enums import Confidence, SubjectType
from app.services import triage
from app.services.policy_gate import GateDecision, PolicyGate
from app.services.triage import TriageClaim
from app.settings import Settings
from app.telemetry import tracer

log = logging.getLogger(__name__)

ToolboxFactory = Callable[[Settings], AbstractAsyncContextManager[Toolbox]]

AWS_AVAILABLE = "AWS tools (CloudTrail, IAM, security groups, Terraform state) are available."
AWS_UNAVAILABLE = (
    "AWS tools are not available in this run, so there is no CloudTrail or IAM evidence. If "
    "the cause looks AWS-side, say which AWS evidence would confirm it and add the "
    "aws_evidence_unavailable flag."
)


def shows_restart(evidence: tuple[tuple[str, str], ...]) -> bool:
    """Whether any `get_pods` result shows a container that restarted or was terminated."""
    for tool, text in evidence:
        if tool != "get_pods":
            continue
        try:
            pods = json.loads(text).get("pods") or []
        except (ValueError, AttributeError):
            continue
        for pod in pods:
            for container in (pod or {}).get("containers") or []:
                if (container or {}).get("restart_count") or (container or {}).get(
                    "last_termination"
                ):
                    return True
    return False


def needs_escalation(result: RunResult) -> bool:
    """Low confidence, or no proposal, on evidence that shows a container restart."""
    analysis = result.analysis
    unsure = (
        analysis is None
        or analysis.confidence is Confidence.low
        or analysis.proposed_remediation is None
    )
    return unsure and shows_restart(result.evidence)


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
        """One `triage.run` span in the incident's trace, around both runs and the gate."""
        model = claim.model or self._settings.triage_model
        started = time.monotonic()
        with tracer.start_as_current_span(
            "triage.run",
            context=telemetry.context_from(claim.traceparent),
            attributes={"incident.id": claim.incident_id, "triage.model": model},
        ) as span:
            result, escalated, tool_calls = await self._run(claim, model)
            span.set_attributes(
                {
                    "triage.outcome": result.outcome.value,
                    "triage.escalated": escalated,
                    "triage.tool_calls": tool_calls,
                }
            )
            telemetry.TRIAGE_DURATION.record(time.monotonic() - started)
            telemetry.TRIAGE_TOOL_CALLS.record(tool_calls)
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

    async def _run(self, claim: TriageClaim, model: str) -> tuple[RunResult, bool, int]:
        """The run, and the escalated re-run if it needs one: (result, escalated, tool calls)."""
        log.info("triage started incident=%s model=%s", claim.incident_id, model)
        try:
            async with self._toolbox_factory(self._settings) as toolbox:
                prompt = incident_prompt(claim, aws_available=toolbox.aws_available)
                result = await self._runner.run(
                    subject_type=SubjectType.incident,
                    subject_id=claim.incident_id,
                    model=model,
                    task_prompt=prompt,
                    toolbox=toolbox,
                )
                escalated = self._may_escalate(claim.incident_id, model) and needs_escalation(
                    result
                )
                if escalated:
                    result = await self._escalate(claim.incident_id, prompt, toolbox, result)
                return result, escalated, toolbox.calls
        except ToolsUnavailable as exc:
            log.error("triage incident=%s: tools unavailable: %s", claim.incident_id, exc)
            result = self._runner.failed_start(
                subject_type=SubjectType.incident,
                subject_id=claim.incident_id,
                model=model,
                detail=f"tools unavailable: {exc}",
            )
            return result, False, 0

    def _may_escalate(self, incident_id: int, model: str) -> bool:
        escalation = self._settings.triage_model_escalation
        if not escalation or model == escalation:
            return False
        with self._uow_factory() as uow:
            return not uow.agent_runs.any_escalated(SubjectType.incident, incident_id)

    async def _escalate(
        self, incident_id: int, prompt: str, toolbox: Toolbox, first: RunResult
    ) -> RunResult:
        model = self._settings.triage_model_escalation
        log.info(
            "triage escalating incident=%s run=%s confidence=%s model=%s",
            incident_id,
            first.run_id,
            first.analysis.confidence.value if first.analysis else "none",
            model,
        )
        result = await self._runner.run(
            subject_type=SubjectType.incident,
            subject_id=incident_id,
            model=model,
            task_prompt=prompt,
            toolbox=toolbox,
            escalated=True,
        )
        # The escalated analysis wins; a run that stopped without one leaves the first's.
        if result.analysis is None and first.analysis is not None:
            return first
        if first.analysis is not None:
            with self._uow_factory() as uow:
                triage.record_superseded_analysis(
                    uow, incident_id, run_id=first.run_id, analysis=first.analysis
                )
        return result

    def _submit(self, incident_id: int, analysis: SubmitAnalysis) -> GateDecision | None:
        proposal = analysis.proposed_remediation
        if proposal is None:
            return None
        with tracer.start_as_current_span(
            "remediation.gate",
            attributes={"incident.id": incident_id, "remediation.kind": proposal.kind},
        ) as span:
            # The gate stores this span's traceparent on the remediation for the apply loop.
            decision = self._gate.submit_proposal(incident_id, proposal)
            span.set_attributes(
                {"remediation.mode": decision.mode.value, "remediation.policy": decision.policy}
            )
        log.info(
            "proposal incident=%s kind=%s gate=%s policy=%s",
            incident_id,
            proposal.kind,
            decision.mode.value,
            decision.policy,
        )
        return decision
