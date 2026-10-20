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
    "worker": {"awsMcp": False},
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


class DuplicateKeyLoader(yaml.SafeLoader):
    """SafeLoader that fails on a key repeated in one mapping, which PyYAML would silently
    collapse to the last value."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        seen: set = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key {key!r}", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _render(release: str, namespace: str, chart: str, values: dict) -> str:
    """Rendered YAML of one of our charts with these values, from plain `helm template`."""
    helm = shutil.which("helm")
    if not helm:
        pytest.fail(
            "neither HELM_RENDER_DIR (from `make helm-lint`) nor helm is available"
        )
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(values, f)
    try:
        return subprocess.run(
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


def helm_template(release: str, namespace: str, chart: str, values: dict) -> list[dict]:
    """`helm template` one of our charts with these values (needs helm, not helmfile)."""
    docs = _load(_render(release, namespace, chart, values))
    for doc in docs:
        doc.setdefault("metadata", {}).setdefault("namespace", namespace)
    return docs


def helm_template_text(config: str) -> list[tuple[str, str, str]]:
    """(chart, namespace, rendered YAML) for each of our releases, with plain `helm template`."""
    return [
        (chart, namespace, _render(release, namespace, chart, values))
        for release, namespace, chart, values in _FALLBACK[config]
    ]


def _helm_template(config: str) -> list[dict]:
    return [doc for args in _FALLBACK[config] for doc in helm_template(*args)]


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
