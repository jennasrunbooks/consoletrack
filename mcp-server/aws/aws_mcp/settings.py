"""aws-mcp configuration, read from environment variables (the chart's aws-mcp.yaml sets them)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# The one state object the role may read (pod-identity.tf → DemoAppState). The server refuses to
# start with any other key, and the AWS wrapper refuses to fetch any other key.
DEMO_APP_STATE_KEY = "demo-app/us-east-1/terraform.tfstate"


class Mode(StrEnum):
    # Real AWS through the Pod Identity role (EKS) or AWS_PROFILE (local sandbox).
    live = "live"
    # Recorded responses for `make demo-replay`. Not implemented yet: the server refuses to start.
    fixtures = "fixtures"


class Transport(StrEnum):
    # Streamable HTTP: the in-cluster server the triage worker connects to.
    http = "http"
    # stdio: Claude Code starts the server itself (local use against a sandbox profile).
    stdio = "stdio"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    aws_mcp_mode: Mode = Mode.live
    aws_region: str = Field(default="us-east-1", pattern=r"^[a-z]{2}(-[a-z]+)+-\d$")

    # IAM paths the IAM tools may read, e.g. /demo-app/. Must match the role's IAM policy
    # (WATCHED_IAM_PATHS in Terraform); the server refuses to start with an empty list.
    watched_iam_paths: Annotated[list[str], NoDecode] = Field(min_length=1)
    # Not used by any tool yet; kept so the chart's env stays meaningful.
    eks_cluster_name: str = ""

    # The demo-app root's state (lookup_terraform_address). An empty bucket means the tool answers
    # with an error instead of guessing.
    tf_state_bucket: str = ""
    tf_state_key: str = DEMO_APP_STATE_KEY

    # LookupEvents allows about 2 requests per second per account: identical lookups within this
    # many seconds are answered from memory.
    lookup_cache_seconds: int = Field(default=120, ge=0, le=900)

    aws_mcp_transport: Transport = Transport.http
    # All interfaces inside the container; the Service decides who reaches it.
    aws_mcp_host: str = "0.0.0.0"
    aws_mcp_port: int = 8080

    # Telemetry (aws_mcp/telemetry.py): none, otlp (OTEL_EXPORTER_OTLP_ENDPOINT), or console.
    telemetry_exporter: Literal["none", "otlp", "console"] = "none"

    @field_validator("watched_iam_paths", mode="before")
    @classmethod
    def _split(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("watched_iam_paths")
    @classmethod
    def _paths(cls, value: list[str]) -> list[str]:
        for path in value:
            # A bare "/" would watch every role in the account.
            if not (path.startswith("/") and path.endswith("/") and len(path) > 2):
                raise ValueError(f"watched IAM path {path!r} must look like /name/")
        return value

    @field_validator("tf_state_key")
    @classmethod
    def _state_key(cls, value: str) -> str:
        if value != DEMO_APP_STATE_KEY:
            raise ValueError(f"TF_STATE_KEY must be {DEMO_APP_STATE_KEY}")
        return value
