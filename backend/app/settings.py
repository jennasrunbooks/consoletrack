"""Backend configuration, read from environment variables with the names in `.env.example`.

Nothing here has a default that is a secret. Secrets (`ALERT_WEBHOOK_TOKEN`,
`GITHUB_WEBHOOK_SECRET`) arrive from Kubernetes Secrets or the shell and are `SecretStr`, so
they never show up in a repr or a log line.
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
