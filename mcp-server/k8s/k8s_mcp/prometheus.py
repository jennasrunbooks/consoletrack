"""Prometheus HTTP API client for query_prometheus (instant and range queries only)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

import httpx


class PrometheusError(Exception):
    """Prometheus unreachable, timed out, or rejected the query."""


class PrometheusReader(Protocol):
    def query(self, promql: str, at: datetime) -> dict[str, Any]: ...

    def query_range(
        self, promql: str, start: datetime, end: datetime, step_seconds: int
    ) -> dict[str, Any]: ...


class HttpPrometheus:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        # The client gives up one second after Prometheus's own `timeout` would have.
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds + 1, transport=transport
        )

    def query(self, promql: str, at: datetime) -> dict[str, Any]:
        return self._get("/api/v1/query", {"query": promql, "time": at.timestamp()})

    def query_range(
        self, promql: str, start: datetime, end: datetime, step_seconds: int
    ) -> dict[str, Any]:
        return self._get(
            "/api/v1/query_range",
            {
                "query": promql,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": step_seconds,
            },
        )

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        # Prometheus stops evaluating at the same limit the client stops waiting.
        params = {**params, "timeout": f"{self._timeout}s"}
        try:
            response = self._http.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise PrometheusError(f"Prometheus timed out after {self._timeout}s") from exc
        except httpx.HTTPError as exc:
            raise PrometheusError(f"Prometheus unreachable: {type(exc).__name__}") from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise PrometheusError(f"Prometheus returned HTTP {response.status_code}") from exc
        if body.get("status") != "success":
            raise PrometheusError(
                f"Prometheus rejected the query: {body.get('errorType', 'error')}: "
                f"{body.get('error', '')}"[:500]
            )
        return body["data"]
