"""kind fixtures: apply the real RBAC, then connect as the k8s-mcp ServiceAccount.

Needs K8S_MCP_KIND_KUBECONFIG, the admin kubeconfig of a throwaway kind cluster that
`scripts/test-k8s.sh` creates (`make test-k8s`, run in CI). Without it every test here is
skipped, so `pytest` collects them anywhere without touching a cluster. Never point it at a real
cluster: the setup applies manifests.
"""

from __future__ import annotations

import copy
import os
import subprocess
from pathlib import Path

import pytest
from kubernetes import client, config

ROOT = Path(__file__).resolve().parents[2]
RBAC = ROOT / "rbac" / "k8s-mcp.yaml"
FIXTURES = Path(__file__).with_name("fixtures.yaml")
SERVICE_ACCOUNT = "system:serviceaccount:consoletrack:k8s-mcp"

KUBECONFIG = os.environ.get("K8S_MCP_KIND_KUBECONFIG")


def kubectl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kubectl", "--kubeconfig", KUBECONFIG, *args],
        capture_output=True,
        text=True,
        check=check,
        timeout=180,
    )


@pytest.fixture(scope="session")
def cluster() -> None:
    kubectl("create", "namespace", "consoletrack", check=False)
    kubectl("create", "namespace", "demo", check=False)
    kubectl("apply", "-f", str(RBAC))
    kubectl("apply", "-f", str(FIXTURES))
    kubectl("-n", "demo", "rollout", "status", "deployment/demo-app", "--timeout=120s")


@pytest.fixture(scope="session")
def sa_api_client(cluster: None) -> client.ApiClient:
    """An API client that authenticates with the ServiceAccount's token, not the admin cert."""
    token = kubectl(
        "-n", "consoletrack", "create", "token", "k8s-mcp", "--duration=10m"
    ).stdout.strip()
    admin = client.Configuration()
    config.load_kube_config(config_file=KUBECONFIG, client_configuration=admin)
    sa = copy.copy(admin)
    sa.cert_file = None
    sa.key_file = None
    sa.api_key = {"authorization": f"Bearer {token}"}
    return client.ApiClient(sa)
