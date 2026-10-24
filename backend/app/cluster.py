"""The backend's two outside calls when it applies a remediation: Kubernetes, as the
`remediator` ServiceAccount, and Prometheus, for the error-ratio check.

`app/services/remediation.py` depends on the `Cluster` and `RatioSource` protocols; unit tests
use fakes. The real `KubernetesCluster` uses in-cluster config on EKS, else KUBECONFIG (on kind,
`make local` writes one with the remediator's own token, so the chart's RBAC limits it there
too). Objects come back as the API's JSON (camelCase), which is what the validator reads.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx2
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

STRATEGIC_MERGE = "application/strategic-merge-patch+json"
FIELD_MANAGER = "consoletrack-remediator"


class ClusterError(Exception):
    """The API server refused or failed a call; the message is safe to record."""


class Cluster(Protocol):
    def get_deployment(self, namespace: str, name: str) -> dict[str, Any]: ...
    def patch_deployment(
        self, namespace: str, name: str, body: dict[str, Any], *, dry_run: bool
    ) -> dict[str, Any]: ...
    def list_pods(self, namespace: str, selector: dict[str, str]) -> list[dict[str, Any]]: ...


class RatioSource(Protocol):
    def ratio(self, promql: str) -> float | None:
        """The query's single value, or None when it has no data (or no traffic)."""
        ...


class KubernetesCluster:
    def __init__(self, request_timeout: float = 10.0) -> None:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self._api = client.ApiClient()
        self._apps = client.AppsV1Api(self._api)
        self._core = client.CoreV1Api(self._api)
        self._timeout = request_timeout

    def get_deployment(self, namespace: str, name: str) -> dict[str, Any]:
        return self._call(
            self._apps.read_namespaced_deployment, name, namespace, _request_timeout=self._timeout
        )

    def patch_deployment(
        self, namespace: str, name: str, body: dict[str, Any], *, dry_run: bool
    ) -> dict[str, Any]:
        return self._call(
            self._apps.patch_namespaced_deployment,
            name,
            namespace,
            body,
            dry_run="All" if dry_run else None,
            field_manager=FIELD_MANAGER,
            _content_type=STRATEGIC_MERGE,
            _request_timeout=self._timeout,
        )

    def list_pods(self, namespace: str, selector: dict[str, str]) -> list[dict[str, Any]]:
        labels = ",".join(f"{key}={value}" for key, value in sorted(selector.items()))
        result = self._call(
            self._core.list_namespaced_pod,
            namespace,
            label_selector=labels,
            _request_timeout=self._timeout,
        )
        return list(result.get("items") or [])

    def _call(self, method: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return self._api.sanitize_for_serialization(method(*args, **kwargs))
        except ApiException as exc:
            # The status and reason only: the body can echo the request.
            raise ClusterError(f"Kubernetes API {exc.status} {exc.reason}") from None
        except Exception as exc:  # connection errors, timeouts
            raise ClusterError(f"Kubernetes API unreachable: {type(exc).__name__}") from None


class PrometheusRatio:
    def __init__(self, base_url: str, timeout_seconds: float = 10.0) -> None:
        self._http = httpx2.Client(base_url=base_url.rstrip("/"), timeout=timeout_seconds)

    def ratio(self, promql: str) -> float | None:
        try:
            response = self._http.get("/api/v1/query", params={"query": promql})
            body = response.json()
        except (httpx2.HTTPError, ValueError):
            return None
        if body.get("status") != "success":
            return None
        result = (body.get("data") or {}).get("result") or []
        if len(result) != 1:
            return None
        try:
            value = float(result[0]["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        # 0/0 (no traffic) is NaN: no evidence either way.
        return None if value != value else value
