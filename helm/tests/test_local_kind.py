"""The consoletrack chart's pieces that `make local` applies to kind (scripts/local-kind.sh).

Rendered with compose/kind-values.yaml and the same --show-only templates as the script: only
ServiceAccounts, k8s-mcp's read-only RBAC, and an AlertmanagerConfig pointed at the Compose
backend. Nothing that runs a pod, and no Secret (the script creates consoletrack-webhook).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from conftest import CHARTS, helm_template

REPO = Path(__file__).resolve().parents[2]
VALUES = REPO / "compose" / "kind-values.yaml"
KIND_OVERRIDE = REPO / "compose" / "kind.yaml"
# scripts/local-kind.sh: `render` in cluster_up, then in alerts.
TEMPLATES = [
    "templates/rbac-k8s-mcp.yaml",
    "templates/serviceaccounts.yaml",
    "templates/monitoring.yaml",
]


@pytest.fixture(scope="module")
def docs() -> list[dict]:
    helm = shutil.which("helm")
    if not helm:
        pytest.fail("helm is not installed")
    args = [arg for t in TEMPLATES for arg in ("--show-only", t)]
    out = subprocess.run(
        [
            helm,
            "template",
            "consoletrack",
            str(CHARTS / "consoletrack"),
            "-n",
            "consoletrack",
        ]
        + ["-f", str(VALUES), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def test_the_script_renders_these_templates() -> None:
    script = (REPO / "scripts" / "local-kind.sh").read_text()
    for template in TEMPLATES:
        assert template in script, template


def test_only_rbac_and_the_alert_route(docs: list[dict]) -> None:
    kinds = {d["kind"] for d in docs}
    assert kinds == {"ServiceAccount", "Role", "RoleBinding", "AlertmanagerConfig"}


def test_alerts_go_to_the_compose_backend(docs: list[dict]) -> None:
    (config,) = [d for d in docs if d["kind"] == "AlertmanagerConfig"]
    (receiver,) = config["spec"]["receivers"]
    (webhook,) = receiver["webhookConfigs"]
    url = webhook["url"]
    assert url == yaml.safe_load(VALUES.read_text())["monitoring"]["webhookUrl"]
    # The backend's alias on kind's Docker network, from compose/kind.yaml.
    aliases = yaml.safe_load(KIND_OVERRIDE.read_text())["services"]["backend"][
        "networks"
    ]
    assert url.startswith(f"http://{aliases['kind']['aliases'][0]}:8000/")
    credentials = webhook["httpConfig"]["authorization"]["credentials"]
    assert credentials == {"name": "consoletrack-webhook", "key": "token"}


def test_in_cluster_url_by_default() -> None:
    docs = helm_template(
        "consoletrack",
        "consoletrack",
        "consoletrack",
        {"target": "local", "app": {"intakeMode": "replay"}},
    )
    (config,) = [d for d in docs if d["kind"] == "AlertmanagerConfig"]
    (webhook,) = config["spec"]["receivers"][0]["webhookConfigs"]
    assert webhook["url"] == (
        "http://backend.consoletrack.svc.cluster.local:8000/api/v1/alerts/webhook"
    )
