"""Pydantic models for the API, hand-written to match `openapi.yaml` (the source of truth).

Class names are the component schema names; `tests/contract/test_openapi.py` fails on any
difference. Response models list every field as required (nullable where the contract says so),
because the contract marks them required. Request models forbid unknown fields
(`additionalProperties: false`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.enums import (
    AgentRunOutcome,
    ChangeDecision,
    ChangeStatus,
    Confidence,
    IncidentStatus,
    IntakeState,
    RemediationKind,
    RemediationMode,
    RemediationStatus,
    Risk,
    Role,
    SubjectType,
    Via,
)

Int64 = Annotated[int, Field(json_schema_extra={"format": "int64"})]
NonNegativeInt = Annotated[int, Field(ge=0)]
Uri = Annotated[str, Field(json_schema_extra={"format": "uri"})]
Note = Annotated[str, Field(max_length=2000)]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


# --- Auth -------------------------------------------------------------------------------------


class LoginRequest(RequestModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256, json_schema_extra={"writeOnly": True})


class User(BaseModel):
    username: str
    role: Role


# --- Changes ----------------------------------------------------------------------------------


class IncidentRef(BaseModel):
    id: Int64
    alertname: str
    namespace: str | None
    workload: str | None
    status: IncidentStatus


class ChangeRef(BaseModel):
    id: Int64
    title: str
    event_name: str
    risk: Risk | None
    status: ChangeStatus


class ChangeSummary(BaseModel):
    id: Int64
    title: str
    event_name: str
    service: str
    event_time: datetime
    detected_at: datetime
    resource_arn: str
    resource_name: str | None
    tf_address: str | None
    principal_arn: str
    session_name: str | None
    via: Via
    risk: Risk | None
    status: ChangeStatus
    linked_incidents: list[IncidentRef]


class LinkedIncident(BaseModel):
    incident: IncidentRef
    confidence: Confidence


class DriftLine(BaseModel):
    op: Literal["context", "added", "removed"]
    text: str
    note: str | None = None


class Drift(BaseModel):
    tf_root: str
    tf_address: str
    attribute: str
    lines: list[DriftLine]


class AgentRun(BaseModel):
    id: Int64
    model: str
    started_at: datetime
    finished_at: datetime | None
    tool_calls: NonNegativeInt
    input_tokens: NonNegativeInt | None
    output_tokens: NonNegativeInt | None
    outcome: AgentRunOutcome


class Analysis(BaseModel):
    id: Int64
    subject_type: SubjectType
    subject_id: Int64
    summary: str
    risk: Risk | None
    risk_reason: str | None
    root_cause: str | None
    confidence: Confidence
    postmortem_md: str | None
    agent_run: AgentRun


class RemediationStep(BaseModel):
    step: str
    at: datetime
    ok: bool
    detail: str


class Remediation(BaseModel):
    id: Int64
    subject_type: SubjectType
    subject_id: Int64
    kind: RemediationKind
    status: RemediationStatus
    payload: dict[str, Any]
    decided_by: str | None
    external_url: Uri | None
    created_at: datetime
    applied_at: datetime | None
    error: str | None
    # The policy gate's decision and the apply path's record (#34). Optional, so clients built
    # before them keep working.
    mode: RemediationMode | None = None
    policy: str | None = None
    gate_reason: str | None = None
    decided_at: datetime | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    steps: list[RemediationStep] = []


class Change(BaseModel):
    id: Int64
    title: str
    event_name: str
    service: str
    event_time: datetime
    detected_at: datetime
    resource_arn: str
    resource_name: str | None
    tf_address: str | None
    principal_arn: str
    session_name: str | None
    via: Via
    risk: Risk | None
    status: ChangeStatus
    cloudtrail_event_id: str
    region: str
    source_ip: str | None
    user_agent: str | None
    request_parameters: dict[str, Any] | None
    decision: ChangeDecision | None
    decided_by: str | None
    decided_at: datetime | None
    reason: str | None
    analysis: Analysis | None
    drift: Drift | None
    remediation: Remediation | None
    linked_incidents: list[LinkedIncident]


class ChangeCounts(BaseModel):
    by_status: dict[ChangeStatus, NonNegativeInt]
    total: NonNegativeInt
    awaiting_review: NonNegativeInt
    high_risk_open: NonNegativeInt
    linked_to_incident: NonNegativeInt
    closed_last_7_days: NonNegativeInt


class ChangePage(BaseModel):
    items: list[ChangeSummary]
    total: NonNegativeInt
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    counts: ChangeCounts


class RevertRequest(RequestModel):
    note: Note | None = None


class RevertResponse(BaseModel):
    change: Change
    run_url: Uri | None


class AcceptRequest(RequestModel):
    note: str = Field(min_length=1, max_length=2000)

    check_note = field_validator("note")(_not_blank)


class CodifyRequest(RequestModel):
    note: Note | None = None


# --- Incidents --------------------------------------------------------------------------------


class IncidentSummary(BaseModel):
    id: Int64
    alertname: str
    severity: str | None
    namespace: str | None
    workload: str | None
    status: IncidentStatus
    opened_at: datetime
    resolved_at: datetime | None
    linked_changes: list[ChangeRef]


class LinkedChange(BaseModel):
    change: ChangeRef
    confidence: Confidence


class Incident(BaseModel):
    id: Int64
    alertname: str
    severity: str | None
    namespace: str | None
    workload: str | None
    status: IncidentStatus
    opened_at: datetime
    resolved_at: datetime | None
    fingerprint: str
    labels: dict[str, str]
    annotations: dict[str, str]
    time_to_resolve_seconds: NonNegativeInt | None
    analysis: Analysis | None
    remediation: Remediation | None
    linked_changes: list[LinkedChange]


class IncidentPage(BaseModel):
    items: list[IncidentSummary]
    total: NonNegativeInt
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class TriageRequest(RequestModel):
    model: Annotated[str, Field(max_length=128)] | None = None


class ApproveRemediationRequest(RequestModel):
    note: Note | None = None


class RejectRemediationRequest(RequestModel):
    reason: str = Field(min_length=1, max_length=2000)

    check_reason = field_validator("reason")(_not_blank)


class PostmortemRequest(RequestModel):
    postmortem_md: str = Field(max_length=100000)


# --- Evidence ---------------------------------------------------------------------------------


class Evidence(BaseModel):
    id: Int64
    agent_run_id: Int64
    seq: int = Field(ge=1)
    tool_name: str
    args: dict[str, Any]
    result_excerpt: str
    created_at: datetime


class EvidenceList(BaseModel):
    items: list[Evidence]


# --- Webhooks ---------------------------------------------------------------------------------


class AlertmanagerAlert(BaseModel):
    status: Literal["firing", "resolved"]
    labels: dict[str, str]
    annotations: dict[str, str]
    startsAt: datetime
    endsAt: datetime | None = None
    generatorURL: str | None = None
    fingerprint: str


class AlertmanagerWebhook(BaseModel):
    """Alertmanager webhook payload, version 4. Unknown fields are ignored."""

    version: Literal["4"]
    groupKey: str | None = None
    truncatedAlerts: NonNegativeInt | None = None
    status: Literal["firing", "resolved"]
    receiver: str | None = None
    groupLabels: dict[str, str] | None = None
    commonLabels: dict[str, str] | None = None
    commonAnnotations: dict[str, str] | None = None
    externalURL: str | None = None
    alerts: list[AlertmanagerAlert]


class AlertWebhookResult(BaseModel):
    opened: list[Int64]
    resolved: list[Int64]
    ignored: NonNegativeInt


class GithubWorkflowRun(BaseModel):
    id: Int64
    name: str | None = None
    display_title: str | None = None
    path: str
    html_url: Uri
    status: str
    conclusion: str | None = None


class GithubWorkflowRunEvent(BaseModel):
    action: Literal["requested", "in_progress", "completed"]
    workflow_run: GithubWorkflowRun


# --- Intake, stats, ops -----------------------------------------------------------------------


class IntakeHealth(BaseModel):
    state: IntakeState
    reasons: list[str]
    region: str
    checked_at: datetime
    last_event_at: datetime | None
    last_canary_at: datetime | None
    queue_backlog: NonNegativeInt | None
    oldest_message_age_seconds: NonNegativeInt | None
    dead_letter_count: NonNegativeInt | None
    trail_logging: bool | None
    grafana_url: Uri | None


class PrincipalCount(BaseModel):
    principal_arn: str
    count: NonNegativeInt


class Stats(BaseModel):
    since: datetime
    until: datetime
    changes_by_principal: list[PrincipalCount]
    median_time_to_document_seconds: NonNegativeInt | None
    reverted: NonNegativeInt
    codified: NonNegativeInt
    accepted: NonNegativeInt


class ProbeStatus(BaseModel):
    status: Literal["ok", "error"]
    checks: dict[str, str] | None = None


# --- Errors -----------------------------------------------------------------------------------


class ErrorDetail(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class Error(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] | None = None
