"""Backend configuration, read from environment variables with the names in `.env.example`.

Nothing here has a default that is a secret. Secrets (`SESSION_SECRET`, the password hashes,
`ALERT_WEBHOOK_TOKEN`, `GITHUB_WEBHOOK_SECRET`) arrive from Kubernetes Secrets or the shell and
are `SecretStr`, so they never show up in a repr or a log line.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class IntakeMode(StrEnum):
    live = "live"
    replay = "replay"


class Settings(BaseSettings):
    # The process environment holds plenty of unrelated variables (AWS_PROFILE, TF_VAR_*, ...).
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # Postgres. Required to serve requests; optional here so tests and `alembic --sql` can
    # build the app or the migration without one.
    database_url: str | None = None

    # The region the intake watches (product-spec.md → AWS design and cost).
    aws_region: str = "us-east-1"

    # Login for the two fixed accounts: argon2id hashes from `make hash-password`. An unset hash
    # means that account can't log in.
    admin_password_hash: SecretStr | None = None
    viewer_password_hash: SecretStr | None = None
    # Signs the session cookie. Required, at least 32 bytes: `create_app` refuses to start
    # without it, and there is no default, not even locally.
    session_secret: SecretStr | None = None

    # Bearer token Alertmanager sends to POST /api/v1/alerts/webhook. Unset means every
    # webhook call is rejected with 401 (fail closed).
    alert_webhook_token: SecretStr | None = None
    # HMAC secret for the optional GitHub workflow_run webhook (#14).
    github_webhook_secret: SecretStr | None = None

    # `replay` is set by `make demo-replay`: recorded CloudTrail fixtures, no AWS (#9, #15).
    intake_mode: IntakeMode = IntakeMode.live
    # The "Intake health" Grafana dashboard, if the browser can reach it.
    grafana_url: str | None = None

    triage_model: str = "claude-haiku-5-5"
    triage_model_escalation: str = "claude-sonnet-5-5"

    # The triage agent's MCP servers (streamable HTTP, e.g. http://k8s-mcp:8080/mcp). Empty
    # AWS_MCP_URL (kind, Compose) means no AWS tools: the agent is told AWS evidence isn't
    # available. ANTHROPIC_API_KEY is read by the Anthropic SDK itself, never by this class.
    k8s_mcp_url: str | None = None
    aws_mcp_url: str | None = None
    # Per-run limits (product-spec.md → Triage agent and guardrails → Runtime).
    triage_max_tool_calls: int = 12
    triage_timeout_seconds: float = 120
    # Input plus output tokens over every model call of one run.
    triage_token_budget: int = 200_000
    triage_max_output_tokens: int = 8_192
    # How long a claimed incident stays the worker's before another worker may claim it again
    # (a crashed run). Longer than the run's own timeout.
    triage_lease_seconds: int = 300
    mcp_call_timeout_seconds: float = 30
    worker_poll_seconds: float = 5
    # Touched after every poll; the worker's probes check its age (helm worker.yaml).
    worker_heartbeat_file: str | None = None

    # Remediation (product-spec.md → Remediation modes; docs/remediation-graduation.md).
    # The chart's remediation-policies ConfigMap. Unset = no policies: every proposal is manual.
    remediation_policies_file: str | None = None
    # The kill switch, next to the file's autoEnabled: auto needs both. Unset or false sends
    # every proposal to a human; the chart sets it from remediation.autoEnabled, and
    # `kubectl set env deploy/worker AUTOFIX_ENABLED=false` turns auto off without a release.
    autofix_enabled: bool = False
    # Namespaces the backend may patch, comma-separated (the chart's allowedNamespaces, one
    # remediator Role each). Empty refuses every cluster remediation.
    k8s_allowed_namespaces: str = ""
    # The backend's apply loop: Kubernetes through the remediator ServiceAccount (in cluster) or
    # KUBECONFIG (make local), and Prometheus for the error-ratio check. Off = approved cluster
    # remediations wait (plain Compose, no cluster).
    remediation_apply_enabled: bool = False
    remediation_poll_seconds: float = 5
    prometheus_url: str | None = None

    @property
    def allowed_namespaces(self) -> frozenset[str]:
        return frozenset(ns.strip() for ns in self.k8s_allowed_namespaces.split(",") if ns.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
