"""Enums shared by the database, the services, and the API.

Class names and values match the enum schemas in `openapi.yaml` exactly; the contract test
fails if they drift. `IncidentDecision` is internal (not part of the API).
"""

from enum import StrEnum


class ChangeStatus(StrEnum):
    detected = "detected"
    awaiting_review = "awaiting_review"
    reverting = "reverting"
    reverted = "reverted"
    accepted = "accepted"
    codifying = "codifying"
    codified = "codified"
    failed = "failed"


# The change log's "Closed" chip.
CLOSED_CHANGE_STATUSES = frozenset(
    {ChangeStatus.reverted, ChangeStatus.accepted, ChangeStatus.codified}
)


class Via(StrEnum):
    console = "console"
    cli = "cli"
    cloudshell = "cloudshell"
    sdk = "sdk"
    terraform_outside_pipeline = "terraform_outside_pipeline"


class Risk(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class Confidence(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class ChangeDecision(StrEnum):
    revert = "revert"
    accept = "accept"
    codify = "codify"


class IncidentStatus(StrEnum):
    open = "open"
    triaging = "triaging"
    awaiting_review = "awaiting_review"
    remediating = "remediating"
    resolved = "resolved"
    closed = "closed"


# Unresolved incidents: a firing alert with the same fingerprint reuses one of these.
ACTIVE_INCIDENT_STATUSES = frozenset(
    {
        IncidentStatus.open,
        IncidentStatus.triaging,
        IncidentStatus.awaiting_review,
        IncidentStatus.remediating,
    }
)


class IncidentDecision(StrEnum):
    resolved = "resolved"
    rejected = "rejected"


class IntakeState(StrEnum):
    live = "live"
    delayed = "delayed"
    down = "down"
    replay = "replay"


class RemediationKind(StrEnum):
    cluster_patch = "cluster_patch"
    set_memory_limit = "set_memory_limit"
    rollback = "rollback"
    revert_workflow = "revert_workflow"
    codify_pr = "codify_pr"


class RemediationStatus(StrEnum):
    proposed = "proposed"
    approved = "approved"
    rejected = "rejected"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class SubjectType(StrEnum):
    change = "change"
    incident = "incident"


class SubjectPath(StrEnum):
    changes = "changes"
    incidents = "incidents"

    @property
    def subject_type(self) -> SubjectType:
        return SubjectType.change if self is SubjectPath.changes else SubjectType.incident


class Role(StrEnum):
    admin = "admin"
    viewer = "viewer"


class AgentRunOutcome(StrEnum):
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    timed_out = "timed_out"
    budget_exceeded = "budget_exceeded"
    limit_reached = "limit_reached"
