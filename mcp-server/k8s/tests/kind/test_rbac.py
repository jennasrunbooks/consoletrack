"""k8s-mcp against a real API server with the RBAC the consoletrack chart ships
(helm/charts/consoletrack/templates/rbac-k8s-mcp.yaml)."""

from __future__ import annotations

import json

import pytest
from fastmcp.exceptions import ToolError
from kubernetes import client
from kubernetes.client import ApiException

from k8s_mcp.kube import KubernetesReader
from k8s_mcp.settings import Settings
from k8s_mcp.tools import K8sTools

from .conftest import KUBECONFIG, SERVICE_ACCOUNT, kubectl

pytestmark = [
    pytest.mark.kind,
    pytest.mark.skipif(not KUBECONFIG, reason="K8S_MCP_KIND_KUBECONFIG unset; run make test-k8s"),
]

DENIED = [
    ("get", "secrets", "demo"),
    ("list", "secrets", "demo"),
    ("watch", "secrets", "demo"),
    ("get", "configmaps", "demo"),
    ("list", "configmaps", "demo"),
    ("create", "pods/exec", "demo"),
    ("create", "pods/attach", "demo"),
    ("create", "pods/portforward", "demo"),
    ("create", "pods", "demo"),
    ("delete", "pods", "demo"),
    ("create", "deployments.apps", "demo"),
    ("update", "deployments.apps", "demo"),
    ("patch", "deployments.apps", "demo"),
    ("delete", "deployments.apps", "demo"),
    ("patch", "deployments.apps/scale", "demo"),
    ("patch", "statefulsets.apps", "demo"),
    ("delete", "replicasets.apps", "demo"),
    ("create", "events", "demo"),
    ("get", "pods", "other"),
    ("list", "pods", "kube-system"),
    ("get", "deployments.apps", "consoletrack"),
    ("get", "secrets", "consoletrack"),
]

ALLOWED = [
    (verb, resource, "demo")
    for verb in ("get", "list", "watch")
    for resource in (
        "pods",
        "pods/log",
        "events",
        "deployments.apps",
        "replicasets.apps",
        "statefulsets.apps",
    )
]


def can_i(verb: str, resource: str, namespace: str) -> bool:
    result = kubectl(
        "auth", "can-i", verb, resource, "-n", namespace, f"--as={SERVICE_ACCOUNT}", check=False
    )
    return result.stdout.strip() == "yes"


@pytest.mark.parametrize(("verb", "resource", "namespace"), DENIED)
def test_rbac_denies(cluster: None, verb: str, resource: str, namespace: str) -> None:
    assert not can_i(verb, resource, namespace)


@pytest.mark.parametrize(("verb", "resource", "namespace"), ALLOWED)
def test_rbac_allows(cluster: None, verb: str, resource: str, namespace: str) -> None:
    assert can_i(verb, resource, namespace)


@pytest.mark.parametrize("resource", ["nodes", "namespaces", "clusterroles"])
def test_rbac_denies_cluster_scoped(cluster: None, resource: str) -> None:
    result = kubectl("auth", "can-i", "list", resource, f"--as={SERVICE_ACCOUNT}", check=False)
    assert result.stdout.strip() == "no"


def test_service_account_token_cannot_read_secrets(sa_api_client: client.ApiClient) -> None:
    # Straight at the API with the token, bypassing k8s-mcp's own checks: RBAC still says no.
    core = client.CoreV1Api(sa_api_client)
    for read in (
        lambda: core.read_namespaced_secret("demo-secrets", "demo"),
        lambda: core.read_namespaced_config_map("demo-config", "demo"),
    ):
        with pytest.raises(ApiException) as exc:
            read()
        assert exc.value.status == 403


@pytest.fixture(scope="module")
def tools(sa_api_client: client.ApiClient) -> K8sTools:
    # `other` is allowlisted here on purpose, to show RBAC refuses it even if the setting is wrong.
    settings = Settings(k8s_allowed_namespaces="demo,other")
    return K8sTools(settings, KubernetesReader(api_client=sa_api_client), None)


def test_get_workload(tools: K8sTools) -> None:
    result = tools.get_workload("demo", "Deployment", "demo-app")

    (app,) = result["pod_template"]["containers"]
    assert app["env_names"] == ["LOG_LEVEL", "API_TOKEN"]
    assert app["resources"]["limits"]["memory"] == "32Mi"
    dumped = json.dumps(result)
    assert "kind-test-literal-value" not in dumped
    assert "kind-test-secret-value" not in dumped


def test_get_pods_and_logs(tools: K8sTools) -> None:
    (pod,) = tools.get_pods("demo", "app=demo-app")["pods"]
    assert pod["phase"] == "Running"

    logs = tools.get_pod_logs("demo", pod["name"], "app", tail=500)

    assert logs["tail"] == 200
    assert "token=[REDACTED]" in logs["untrusted_data"]
    assert "kind-log-token-1234" not in logs["untrusted_data"]


def test_get_events(tools: K8sTools) -> None:
    result = tools.get_events("demo", "Deployment/demo-app")
    assert isinstance(result["untrusted_data"], list)


def test_get_rollout_history(tools: K8sTools) -> None:
    result = tools.get_rollout_history("demo", "demo-app")
    assert result["revisions"][0]["images"] == {"app": "busybox:1.37.0"}


def test_namespace_without_a_role_is_forbidden(tools: K8sTools) -> None:
    with pytest.raises(ToolError, match="forbidden by the k8s-mcp ServiceAccount's RBAC"):
        tools.get_workload("other", "Deployment", "hidden")


def test_namespace_outside_allowlist_is_refused(tools: K8sTools) -> None:
    with pytest.raises(ToolError, match="outside K8S_ALLOWED_NAMESPACES"):
        tools.get_pods("kube-system")
