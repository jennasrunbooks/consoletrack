"""The demo-app chart's fault and traffic-generator wiring (#51, #60).

The OOM fault has to fit the oom-memory-bump policy in the consoletrack chart: under the fault's
traffic the app outgrows its unchanged limit and is OOMKilled, and the correct fix (2x the limit)
is within the policy's bounds, so the policy gate and #17's validator accept it. The fault lives
in the traffic generator, so the app's Deployment has no new revision to roll back to.
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
    assert app["resources"]["limits"]["memory"] == "128Mi"
    assert app["resources"]["requests"]["memory"] == "64Mi"
    assert env(app)["DEMO_REPORT_MI"] == "2"
    assert env(app)["DEMO_REPORT_CACHE_MI"] == "140"
    assert "LOADGEN_REPORT" not in env(render()["demo-app-loadgen"])


def test_oom_fault_leaves_the_app_alone() -> None:
    # No new demo-app revision: the limit, env, and everything else stay as they were.
    assert render(faults={"oom": True})["demo-app"] == render()["demo-app"]
    assert (
        env(render(faults={"oom": True})["demo-app-loadgen"])["LOADGEN_REPORT"]
        == "large"
    )


def test_oom_fix_fits_the_policy_bounds() -> None:
    values = yaml.safe_load((CHARTS / "demo-app" / "values.yaml").read_text())
    policies = yaml.safe_load((CHARTS / "consoletrack" / "values.yaml").read_text())
    (oom,) = [
        p for p in policies["remediation"]["policies"] if p["name"] == "oom-memory-bump"
    ]
    bounds = oom["bounds"]
    limit = mem(
        render(faults={"oom": True})["demo-app"]["resources"]["limits"]["memory"]
    )
    cache = values["reportCache"]["capMi"] * 2**20
    fixed = 2 * limit  # set_memory_limit to 256Mi
    # The app is ~19 MiB under the fault on EKS and kind (#79): with a full cache it passes the
    # limit with room to spare, so the fault OOMs.
    app = 19 * 2**20
    assert cache + app - limit >= 24 * 2**20
    # It fills before the app's first scrape: well under 30s at loadgen.rps.
    per_second = (
        values["loadgen"]["rps"] * values["reportCache"]["perRequestMi"] * 2**20
    )
    assert (limit - app) / per_second < 15
    assert fixed <= bounds["maxMultiplier"] * limit
    assert fixed <= mem(bounds["ceiling"])
    # A full cache and the app fit in the fix with room to spare: 97 MiB measured.
    assert fixed - cache - app >= 64 * 2**20


LOADGEN_ENV = {
    "DEMO_MODE": "loadgen",
    "PORT": "8080",
    "LOADGEN_TARGET": "http://demo-app.demo:8080/",
    "LOADGEN_RPS": "5",
    "LOADGEN_TIMEOUT_SECONDS": "1",
}


@pytest.mark.parametrize(
    "faults", [{}, {"oom": True}, {"badImage": True}, {"badProbe": True}]
)
def test_loadgen_ignores_the_app_faults(faults: dict) -> None:
    lg = render(faults=faults)["demo-app-loadgen"]
    assert lg["image"] == "ghcr.io/jennasrunbooks/consoletrack-demo-app:0.1.0"
    assert lg["livenessProbe"]["httpGet"]["path"] == "/healthz"
    assert lg["resources"] == render()["demo-app-loadgen"]["resources"]
    # The OOM fault's header is the only difference.
    expected = (
        {**LOADGEN_ENV, "LOADGEN_REPORT": "large"} if faults.get("oom") else LOADGEN_ENV
    )
    assert env(lg) == expected


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
        # The consoletrack AlertmanagerConfig sends only alerts with this label (#79).
        "consoletrack_incident": "true",
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
