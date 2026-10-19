"""k8s-mcp configuration, read from environment variables (see `.env.example`)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Transport(StrEnum):
    # Streamable HTTP: the in-cluster server the triage worker connects to.
    http = "http"
    # stdio: Claude Code starts the server itself through `.mcp.json` (local kind only).
    stdio = "stdio"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # The only namespaces any tool may read. Must match the namespaces that have a k8s-mcp Role
    # (rbac/k8s-mcp.yaml); the server refuses to start with an empty list (fail closed).
    k8s_allowed_namespaces: Annotated[list[str], NoDecode] = Field(min_length=1)
    # kubeconfig context for local use. Ignored in-cluster, where the ServiceAccount token is used.
    k8s_context: str | None = None

    # Prometheus base URL, e.g. http://kube-prometheus-stack-prometheus.monitoring:9090.
    # Unset means query_prometheus answers with an error instead of guessing.
    prometheus_url: str | None = None
    prometheus_timeout_seconds: float = Field(default=10.0, gt=0, le=30)
    # Longest range query_prometheus accepts.
    prometheus_max_minutes: int = Field(default=60, ge=1, le=360)

    k8s_mcp_transport: Transport = Transport.http
    # All interfaces inside the container; the Service and NetworkPolicy decide who reaches it.
    k8s_mcp_host: str = "0.0.0.0"
    k8s_mcp_port: int = 8080

    @field_validator("k8s_allowed_namespaces", mode="before")
    @classmethod
    def _split(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value
