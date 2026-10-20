"""The policy gate's interface: where an agent proposal stops (product-spec.md → Remediation
modes).

The worker records the proposal as a `proposed` remediation, then calls
`PolicyGate.submit_proposal(incident_id, proposal)`. #34 implements the validator, the per-kind
modes, the kill switch, the rate limit, and the apply-and-verify path behind this interface.
Until then `ManualPolicyGate` is the only gate: every proposal waits for an admin, and nothing
here can apply anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.agents.schema import Proposal


class GateMode(StrEnum):
    manual = "manual"
    auto = "auto"


@dataclass(frozen=True)
class GateDecision:
    mode: GateMode
    # True only when the gate approves on a human's behalf (`auto`); `manual` never approves.
    approved: bool
    # The policy that decided, e.g. `default` or `oom-memory-bump`.
    policy: str
    reason: str


class PolicyGate(Protocol):
    def submit_proposal(self, incident_id: int, proposal: Proposal) -> GateDecision: ...


MANUAL_REASON = "Phase 1 runs every remediation policy in manual mode: an admin approves or rejects"


class ManualPolicyGate:
    """Every proposal goes to a human. The Phase 1 default, and the fallback when in doubt."""

    def submit_proposal(self, incident_id: int, proposal: Proposal) -> GateDecision:
        return GateDecision(
            mode=GateMode.manual, approved=False, policy="default", reason=MANUAL_REASON
        )
