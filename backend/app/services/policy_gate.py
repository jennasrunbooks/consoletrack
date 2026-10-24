"""The policy gate: where an agent proposal stops (product-spec.md → Remediation modes;
docs/remediation-graduation.md).

The worker records the proposal as a `proposed` remediation, then calls
`PolicyGate.submit_proposal(incident_id, proposal)`. The gate never applies anything: `auto`
approves on a human's behalf (`decided_by = policy:<name>`) and the backend's apply loop
(`app/services/remediation.py`) does the rest under the `remediator` ServiceAccount.

`ConfiguredPolicyGate` answers `auto` only when every check passes, in this order, and
otherwise `manual` with the reason recorded on the remediation:

1. the matching policy is `auto` (the chart ships only `oom-memory-bump` that way; AWS-side
   kinds can't be, remediation_policy.py);
2. the kill switch is on: the file's `autoEnabled` and AUTOFIX_ENABLED;
3. the agent's confidence is at least the policy's `minConfidence`;
4. no earlier auto fix of this workload failed (a failure turns auto off for it for good);
5. the rate limit: at most `perWorkload` auto fixes of the workload per `window`;
6. the validator's cluster-free checks (namespace, allowed fields, ceiling). The backend runs
   the full validator against the live Deployment before applying.

`ManualPolicyGate` sends everything to a human; it's the fallback in tests.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app import telemetry
from app.agents.schema import Proposal, Rollback, SetMemoryLimit
from app.db.models import IncidentRow, RemediationRow
from app.db.repositories import UnitOfWork
from app.enums import (
    AWS_REMEDIATION_KINDS,
    Confidence,
    IncidentStatus,
    RemediationKind,
    RemediationMode,
    RemediationStatus,
    SubjectType,
)
from app.services import incident_state, remediation_state
from app.services.incident_state import IncidentEvent
from app.services.remediation_policy import Policy, PolicyConfig
from app.services.remediation_state import RemediationEvent
from app.services.remediation_validator import Refused, check_proposal
from app.settings import Settings

log = logging.getLogger(__name__)

# Kept for callers that predate RemediationMode.
GateMode = RemediationMode

CONFIDENCE_ORDER = (Confidence.low, Confidence.medium, Confidence.high)


@dataclass(frozen=True)
class GateDecision:
    mode: RemediationMode
    # True only when the gate approves on a human's behalf (`auto`); `manual` never approves.
    approved: bool
    # The policy that decided, e.g. `default` or `oom-memory-bump`.
    policy: str
    reason: str


class PolicyGate(Protocol):
    def submit_proposal(self, incident_id: int, proposal: Proposal) -> GateDecision: ...


MANUAL_REASON = "Every remediation policy runs in manual mode here: an admin approves or rejects"


class ManualPolicyGate:
    """Every proposal goes to a human."""

    def submit_proposal(self, incident_id: int, proposal: Proposal) -> GateDecision:
        return GateDecision(
            mode=RemediationMode.manual, approved=False, policy="default", reason=MANUAL_REASON
        )


def target(proposal: Proposal) -> tuple[str | None, str | None]:
    """The (namespace, workload) a cluster proposal names; (None, None) for AWS-side kinds."""
    if isinstance(proposal, SetMemoryLimit | Rollback):
        return proposal.namespace, proposal.deployment
    return None, None


class ConfiguredPolicyGate:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        config: PolicyConfig,
        settings: Settings,
        clock: Callable[[], datetime],
    ) -> None:
        self._uow_factory = uow_factory
        self._config = config
        self._settings = settings
        self._clock = clock

    def submit_proposal(self, incident_id: int, proposal: Proposal) -> GateDecision:
        kind = RemediationKind(proposal.kind)
        namespace, workload = target(proposal)
        policy = self._config.policy_for(kind, namespace)
        now = self._clock()
        with self._uow_factory() as uow:
            row = uow.remediations.latest_for(SubjectType.incident, incident_id, for_update=True)
            incident = uow.incidents.get(incident_id, for_update=True)
            if row is None or incident is None or row.status is not RemediationStatus.proposed:
                return GateDecision(
                    mode=RemediationMode.manual,
                    approved=False,
                    policy=policy.name,
                    reason="no proposed remediation to decide on",
                )
            row.namespace, row.workload = namespace, workload
            # The worker's `remediation.gate` span: the apply loop continues this trace.
            row.traceparent = telemetry.current_traceparent()
            reason = self._refusal(uow, row, incident, proposal, policy, now)
            if reason is None:
                mode, reason = RemediationMode.auto, f"policy {policy.name} applies it"
                remediation_state.apply(
                    row, RemediationEvent.approved, now=now, actor=f"policy:{policy.name}"
                )
                incident_state.apply(incident, IncidentEvent.remediation_approved, now=now)
            else:
                mode = RemediationMode.manual
            row.mode, row.policy, row.gate_reason = mode, policy.name, reason
            uow.commit()
        log.info(
            "gate incident=%s kind=%s policy=%s mode=%s reason=%s",
            incident_id,
            kind.value,
            policy.name,
            mode.value,
            reason,
        )
        return GateDecision(
            mode=mode, approved=mode is RemediationMode.auto, policy=policy.name, reason=reason
        )

    def _refusal(
        self,
        uow: UnitOfWork,
        row: RemediationRow,
        incident: IncidentRow,
        proposal: Proposal,
        policy: Policy,
        now: datetime,
    ) -> str | None:
        """Why this proposal can't go auto, or None when it can."""
        if policy.mode is not RemediationMode.auto:
            return f"policy {policy.name} is manual: an admin approves or rejects"
        if row.kind in AWS_REMEDIATION_KINDS:
            return f"{row.kind.value} changes AWS: always manual (Revert or Codify)"
        if not self._config.auto_enabled:
            return "kill switch: remediation.autoEnabled is false"
        if not self._settings.autofix_enabled:
            return "kill switch: AUTOFIX_ENABLED is false"
        if incident.status is not IncidentStatus.awaiting_review:
            return f"the incident is {incident.status.value}, not awaiting_review"
        if policy.min_confidence is not None:
            analysis = uow.analyses.latest_for(SubjectType.incident, incident.id)
            confidence = analysis.confidence if analysis is not None else Confidence.low
            if CONFIDENCE_ORDER.index(confidence) < CONFIDENCE_ORDER.index(policy.min_confidence):
                return (
                    f"the agent's confidence is {confidence.value}; policy {policy.name} needs "
                    f"{policy.min_confidence.value}"
                )
        if row.namespace is None or row.workload is None:
            return "the proposal names no workload"
        earlier = uow.remediations.auto_for_workload(row.namespace, row.workload)
        history = [r for r in earlier if r is not row]
        failed = [r for r in history if r.status is RemediationStatus.failed]
        if failed:
            return (
                f"an auto fix of {row.namespace}/{row.workload} failed before (remediation "
                f"{failed[-1].id}): auto stays off for this workload"
            )
        since = now - policy.rate_limit.window
        recent = [r for r in history if r.decided_at is not None and r.decided_at >= since]
        if len(recent) >= policy.rate_limit.per_workload:
            return (
                f"rate limit: {len(recent)} auto fix(es) of {row.namespace}/{row.workload} in "
                f"the last {policy.rate_limit.window}"
            )
        if isinstance(proposal, SetMemoryLimit):
            try:
                check_proposal(
                    proposal, policy=policy, allowed_namespaces=self._settings.allowed_namespaces
                )
            except Refused as exc:
                return f"validator: {exc}"
        return None
