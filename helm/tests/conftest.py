"""Rendered manifests for the tests in this folder, one set per deployment configuration.

`make helm-lint` renders the whole Helmfile with `helmfile template --output-dir` for each
configuration into HELM_RENDER_DIR/<config>/ and runs these tests on the result. Without
HELM_RENDER_DIR (no helmfile installed, e.g. the devcontainer), the tests render ConsoleTrack's
own charts with plain `helm template` instead, with the chart values Helmfile would pass.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

HELM_DIR = Path(__file__).resolve().parents[1]
CHARTS = HELM_DIR / "charts"

# Configurations `make helm-lint` renders (keep the names in sync with the Makefile).
CONFIGS = ("eks", "eks-alb", "local")

# Our releases that a namespaced deploy identity applies (tier=app), and every chart of ours.
APP_CHARTS = ("consoletrack", "demo-app")
OUR_CHARTS = (*APP_CHARTS, "cluster-defaults")

_EKS_AWS = {
    "nameSuffix": "test",
    "clusterName": "ct-eks-use1-test",
    "changesQueueUrl": "https://sqs.us-east-1.amazonaws.com/123456789012/ct-changes-use1-test",
    "changesDlqUrl": "https://sqs.us-east-1.amazonaws.com/123456789012/ct-changes-dlq-use1-test",
    "trailName": "ct-trail-use1-test",
    "canarySecurityGroupId": "sg-0123456789abcdef0",
}
_ALB = {
    "exposure": "alb",
    "domain": "consoletrack.example.com",
    "allowedCidrs": ["203.0.113.0/24", "198.51.100.7/32"],
    "certificateArn": "arn:aws:acm:us-east-1:123456789012:certificate/"
    "00000000-0000-0000-0000-000000000000",
}
_LOCAL = {
    "target": "local",
    "image": {"tag": "local"},
    "app": {"intakeMode": "replay"},
    "awsMcp": {"mode": "fixtures"},
    "postgres": {"storage": {"storageClass": "standard"}},
}

# Fallback: (release, namespace, chart, values) per configuration, mirroring helmfile.yaml.gotmpl.
_FALLBACK = {
    "eks": [
        (
            "demo-app",
            "demo",
            "demo-app",
            {"aws": {"bucket": "demo-app-data-use1-test"}},
        ),
        ("consoletrack", "consoletrack", "consoletrack", {"aws": _EKS_AWS}),
        ("cluster-defaults", "kube-system", "cluster-defaults", {}),
    ],
    "eks-alb": [
        (
            "demo-app",
            "demo",
            "demo-app",
            {"aws": {"bucket": "demo-app-data-use1-test"}},
        ),
        ("consoletrack", "consoletrack", "consoletrack", {"aws": _EKS_AWS, **_ALB}),
        (
            "cluster-defaults",
            "kube-system",
            "cluster-defaults",
            {"albGatewayClass": {"enabled": True}},
        ),
    ],
    "local": [
        ("demo-app", "demo", "demo-app", {"image": {"tag": "local"}}),
        ("consoletrack", "consoletrack", "consoletrack", _LOCAL),
        (
            "cluster-defaults",
            "kube-system",
            "cluster-defaults",
            {"storageClass": {"enabled": False}},
        ),
    ],
}


def _load(text: str) -> list[dict]:
    return [doc for doc in yaml.safe_load_all(text) if doc]


def _helm_template(config: str) -> list[dict]:
    helm = shutil.which("helm")
    if not helm:
        pytest.fail(
            "neither HELM_RENDER_DIR (from `make helm-lint`) nor helm is available"
        )
    docs: list[dict] = []
    for release, namespace, chart, values in _FALLBACK[config]:
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            yaml.safe_dump(values, f)
        try:
            out = subprocess.run(
                [
                    helm,
                    "template",
                    release,
                    str(CHARTS / chart),
                    "-n",
                    namespace,
                    "-f",
                    f.name,
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        finally:
            os.unlink(f.name)
        for doc in _load(out):
            doc.setdefault("metadata", {}).setdefault("namespace", namespace)
            docs.append(doc)
    return docs


@pytest.fixture(scope="session", params=CONFIGS)
def config(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture(scope="session")
def rendered() -> dict[str, list[dict]]:
    render_dir = os.environ.get("HELM_RENDER_DIR")
    if render_dir:
        # `helmfile template --output-dir <dir>/<config>`: one file per template, per release.
        out = {}
        for c in CONFIGS:
            files = sorted((Path(render_dir) / c).rglob("*.yaml"))
            out[c] = [doc for f in files for doc in _load(f.read_text())]
            if not out[c]:
                pytest.fail(f"no rendered manifests in {render_dir}/{c}")
        return out
    return {c: _helm_template(c) for c in CONFIGS}


def chart_of(doc: dict) -> str:
    """Chart name from the helm.sh/chart label (`<name>-<version>`), or "" for upstream objects."""
    label = (doc.get("metadata", {}).get("labels") or {}).get("helm.sh/chart", "")
    return label.rsplit("-", 1)[0] if label else ""


@pytest.fixture
def ours(rendered: dict[str, list[dict]], config: str) -> list[dict]:
    """Objects from our charts in this configuration (third-party add-ons left out)."""
    return [d for d in rendered[config] if chart_of(d) in OUR_CHARTS]


@pytest.fixture
def app_objects(ours: list[dict]) -> list[dict]:
    """Objects from the tier=app charts, which CI's namespaced deploy role applies."""
    return [d for d in ours if chart_of(d) in APP_CHARTS]
