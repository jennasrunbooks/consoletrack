"""The demo-app chart's fault and traffic-generator wiring (#51).

The OOM fault has to fit the oom-memory-bump policy in the consoletrack chart: the faulted pod is
OOMKilled, and the correct fix (back to the normal limit) is within the policy's bounds, so the
policy gate and #17's validator accept it.
"""

from __future__ import annotations

import pytest
import yaml
from conftest import CHARTS, helm_template

UNITS = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30}


def mem(quantity: str) -> int:
    for suffix, factor in UNITS.items():
        if quantity.endswith(suffix):
            return int(quantity[: -len(suffix)]) * factor
    return int(quantity)


def render(**values: object) -> dict[str, dict]:
    """The demo-app chart's Deployments' first container, by Deployment name."""
    docs = helm_template("demo-app", "demo", "demo-app", values)
    return {
        d["metadata"]["name"]: d["spec"]["template"]["spec"]["containers"][0]
        for d in docs
        if d["kind"] == "Deployment"
    }


def env(container: dict) -> dict[str, str]:
    return {e["name"]: e["value"] for e in container.get("env", [])}


def test_normal_sizing() -> None:
    app = render()["demo-app"]
    assert app["resources"]["limits"]["memory"] == "256Mi"
    assert app["resources"]["requests"]["memory"] == "128Mi"
    assert "DEMO_ALLOCATE_MI" not in env(app)


def test_oom_fault() -> None:
    app = render(faults={"oom": True})["demo-app"]
    assert app["resources"]["limits"]["memory"] == "128Mi"
    assert app["resources"]["requests"]["memory"] == "128Mi"
    assert env(app)["DEMO_ALLOCATE_MI"] == "128"


def test_oom_fix_fits_the_policy_bounds() -> None:
    policies = yaml.safe_load((CHARTS / "consoletrack" / "values.yaml").read_text())
    (oom,) = [
        p for p in policies["remediation"]["policies"] if p["name"] == "oom-memory-bump"
    ]
    bounds = oom["bounds"]
    faulted = render(faults={"oom": True})["demo-app"]["resources"]["limits"]["memory"]
    fixed = render()["demo-app"]["resources"]["limits"]["memory"]
    # The allocation alone reaches the faulted limit, so the OOM doesn't depend on the baseline.
    allocated = (
        int(env(render(faults={"oom": True})["demo-app"])["DEMO_ALLOCATE_MI"]) * 2**20
    )
    assert allocated >= mem(faulted)
    assert mem(fixed) <= bounds["maxMultiplier"] * mem(faulted)
    assert mem(fixed) <= mem(bounds["ceiling"])
    # The app's ~50 MiB on EKS (boto3 loaded) still fits next to the allocation once fixed.
    assert mem(fixed) - allocated >= 64 * 2**20


@pytest.mark.parametrize(
    "faults", [{}, {"oom": True}, {"badImage": True}, {"badProbe": True}]
)
def test_loadgen_ignores_the_faults(faults: dict) -> None:
    lg = render(faults=faults)["demo-app-loadgen"]
    assert lg["image"] == "ghcr.io/jennasrunbooks/consoletrack-demo-app:0.1.0"
    assert lg["livenessProbe"]["httpGet"]["path"] == "/healthz"
    assert lg["resources"] == render()["demo-app-loadgen"]["resources"]
    assert env(lg) == {
        "DEMO_MODE": "loadgen",
        "PORT": "8080",
        "LOADGEN_TARGET": "http://demo-app.demo:8080/",
        "LOADGEN_RPS": "5",
        "LOADGEN_TIMEOUT_SECONDS": "1",
    }


def test_loadgen_is_its_own_scrape_target() -> None:
    docs = helm_template("demo-app", "demo", "demo-app", {})
    services = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Service"}
    monitors = {d["metadata"]["name"]: d for d in docs if d["kind"] == "ServiceMonitor"}
    assert set(monitors) == {"demo-app", "demo-app-loadgen"}
    loadgen_pod_labels = next(
        d["spec"]["template"]["metadata"]["labels"]
        for d in docs
        if d["kind"] == "Deployment" and d["metadata"]["name"] == "demo-app-loadgen"
    )
    # The app's Service must not send traffic to the loadgen pod; the loadgen's must select it.
    app_selector = services["demo-app"]["spec"]["selector"]
    assert not app_selector.items() <= loadgen_pod_labels.items()
    assert (
        services["demo-app-loadgen"]["spec"]["selector"].items()
        <= loadgen_pod_labels.items()
    )
    sm = monitors["demo-app-loadgen"]["spec"]
    assert (
        sm["selector"]["matchLabels"]
        == services["demo-app-loadgen"]["spec"]["selector"]
    )


def test_loadgen_can_be_turned_off() -> None:
    assert set(render(loadgen={"enabled": False})) == {"demo-app"}


def rules(**values: object) -> dict[str, dict]:
    docs = helm_template("demo-app", "demo", "demo-app", values)
    return {
        rule["alert"]: rule
        for d in docs
        if d["kind"] == "PrometheusRule"
        for group in d["spec"]["groups"]
        for rule in group["rules"]
    }


def test_high_error_rate_alert_routes_to_consoletrack() -> None:
    rule = rules()["DemoAppHighErrorRate"]
    # namespace is what the consoletrack AlertmanagerConfig routes on (sum() drops it from the
    # series); workload names the Deployment for the incident.
    assert rule["labels"] == {
        "severity": "critical",
        "namespace": "demo",
        "workload": "demo-app",
    }
    assert rule["for"] == "1m"
    assert 'namespace="demo"' in rule["expr"]
    assert "demo_client_requests_total" in rule["expr"]


def test_alert_rules_can_be_turned_off() -> None:
    assert rules(alertRules={"enabled": False}) == {}


def test_faults_reach_users() -> None:
    """A rolling update would keep the old pod serving behind a faulted one that's never Ready."""
    docs = helm_template("demo-app", "demo", "demo-app", {"faults": {"oom": True}})
    app = next(
        d
        for d in docs
        if d["kind"] == "Deployment" and d["metadata"]["name"] == "demo-app"
    )
    assert app["spec"]["strategy"] == {"type": "Recreate"}
