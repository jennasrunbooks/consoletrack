"""Inbound webhooks: Alertmanager (bearer token) and GitHub workflow_run (HMAC signature)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Security
from fastapi.security import APIKeyHeader

from app.api.deps import Now, Uow, require_webhook_token
from app.api.errors import NotImplementedYet, error_responses
from app.schemas import AlertmanagerWebhook, AlertWebhookResult, GithubWorkflowRunEvent
from app.services import alerts

router = APIRouter(prefix="/api/v1", tags=["webhooks"])

github_signature = APIKeyHeader(
    name="X-Hub-Signature-256", scheme_name="githubSignature", auto_error=False
)


@router.post(
    "/alerts/webhook",
    operation_id="receiveAlertmanagerWebhook",
    status_code=202,
    response_model=AlertWebhookResult,
    responses=error_responses(400, 401, 422),
    dependencies=[Depends(require_webhook_token)],
)
def receive_alertmanager_webhook(
    payload: AlertmanagerWebhook, uow: Uow, now: Now
) -> AlertWebhookResult:
    """Ingest firing and resolved alerts from Alertmanager."""
    return alerts.process_alertmanager(uow, payload, now=now)


@router.post(
    "/integrations/github/workflow-run",
    operation_id="receiveGithubWorkflowRun",
    status_code=204,
    responses=error_responses(400, 401, 422),
)
def receive_github_workflow_run(
    event: GithubWorkflowRunEvent,
    x_github_event: Annotated[str, Header(alias="X-GitHub-Event")],
    x_github_delivery: Annotated[str, Header(alias="X-GitHub-Delivery")],
    signature: Annotated[str | None, Security(github_signature)],
) -> None:
    """Update a revert's status when its workflow run finishes (optional)."""
    raise NotImplementedYet("POST /api/v1/integrations/github/workflow-run", "#14 (gated Revert)")
