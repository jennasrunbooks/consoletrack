"""Checks over the rendered manifests of ConsoleTrack's own charts (#12 acceptance criteria).

Every container: requests and limits, probes (long-running ones), non-root, no privilege
escalation, read-only root filesystem, all capabilities dropped. Secrets are referenced by name
only. RBAC is namespaced, read-only for k8s-mcp, and patch-only for the remediator.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

import pytest
import yaml
from conftest import (
    APP_CHARTS,
    AWS_MCP_ENABLED,
    HELM_DIR,
    INCIDENT_JOURNEY,
    OBSERVABILITY_CHARTS,
    DuplicateKeyLoader,
    chart_of,
    helm_template,
    helm_template_text,
)

WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}
# Secrets the charts may read: created by scripts/create-secrets.sh, or by CloudNativePG.
ALLOWED_SECRETS = {
    "consoletrack-api-key",
    "consoletrack-auth",
    "consoletrack-webhook",
    "consoletrack-db-app",
}
# The only pods that call the Kubernetes API.
TOKEN_ACCOUNTS = {"remediator", "k8s-mcp"}
# Pod Identity associations in terraform/consoletrack/us-east-1 and terraform/demo-app/us-east-1.
POD_IDENTITY_ACCOUNTS = {
    ("consoletrack", "worker"),
    ("consoletrack", "aws-mcp"),
    ("demo", "demo-app"),
}
CLUSTER_SCOPED_KINDS = {
    "Namespace", "ClusterRole", "ClusterRoleBinding", "CustomResourceDefinition", "StorageClass",
    "GatewayClass", "PersistentVolume", "MutatingWebhookConfiguration",
    "ValidatingWebhookConfiguration", "ValidatingAdmissionPolicy", "PriorityClass",
}  # fmt: skip
SENSITIVE_ENV = re.compile(
    r"(^|_)(API_KEY|ACCESS_KEY|TOKEN|SECRET|PASSWORD)(_|$)", re.IGNORECASE
)


def pod_specs(docs: list[dict]) -> list[tuple[dict, dict]]:
    """(workload, pod spec) for every workload."""
    out = []
    for d in docs:
        if d["kind"] not in WORKLOAD_KINDS:
            continue
        spec = d["spec"]
        if d["kind"] == "CronJob":
            spec = spec["jobTemplate"]["spec"]
        out.append((d, spec["template"]["spec"]))
    return out


def containers(docs: list[dict]) -> list[tuple[dict, dict, dict, bool]]:
    """(workload, pod spec, container, is_init) for every container."""
    out = []
    for workload, pod in pod_specs(docs):
        out += [(workload, pod, c, True) for c in pod.get("initContainers", [])]
        out += [(workload, pod, c, False) for c in pod["containers"]]
    return out


def name(workload: dict, container: dict) -> str:
    return f"{workload['kind']}/{workload['metadata']['name']}:{container['name']}"


def test_renders_our_workloads(ours: list[dict], config: str) -> None:
    names = {d["metadata"]["name"] for d in ours if d["kind"] == "Deployment"}
    assert names >= {
        "backend", "worker", "frontend", "k8s-mcp", "demo-app", "demo-app-loadgen",
    }  # fmt: skip
    # aws-mcp runs on EKS; on kind it waits for its fixtures mode.
    assert ("aws-mcp" in names) is AWS_MCP_ENABLED[config]
    has_canary = any(
        d["kind"] == "CronJob" and d["metadata"]["name"] == "canary" for d in ours
    )
    assert has_canary == (config != "local")


def test_only_user_impact_rules_open_incidents(ours: list[dict], config: str) -> None:
    """The consoletrack AlertmanagerConfig routes only rules labelled consoletrack_incident (#79):
    the user-impact alert and the canary (EKS only). Every rule of ours is one of those."""
    rules = [
        rule
        for d in ours
        if d["kind"] == "PrometheusRule"
        for group in d["spec"]["groups"]
        for rule in group["rules"]
        if "alert" in rule
    ]
    incidents = {
        r["alert"] for r in rules if r["labels"].get("consoletrack_incident") == "true"
    }
    expected = {"DemoAppHighErrorRate"}
    if config != "local":
        expected.add("ConsoleTrackCanaryMissing")
    assert incidents == expected
    assert all(r["labels"].get("consoletrack_incident") == "true" for r in rules)


def test_no_secret_objects(ours: list[dict]) -> None:
    assert [d["metadata"]["name"] for d in ours if d["kind"] == "Secret"] == []


def test_secrets_referenced_by_name_only(ours: list[dict]) -> None:
    for workload, _, c, _ in containers(ours):
        for env in c.get("env", []):
            ref = env.get("valueFrom", {}).get("secretKeyRef")
            if ref:
                assert ref["name"] in ALLOWED_SECRETS, name(workload, c)
            elif SENSITIVE_ENV.search(env["name"]):
                pytest.fail(
                    f"{name(workload, c)} sets {env['name']} from a literal value"
                )
        for source in c.get("envFrom", []):
            assert "secretRef" not in source, (
                f"{name(workload, c)}: use secretKeyRef per key"
            )


@pytest.mark.parametrize("field", ["requests", "limits"])
def test_every_container_has_resources(ours: list[dict], field: str) -> None:
    for workload, _, c, _ in containers(ours):
        res = c.get("resources", {}).get(field, {})
        assert {"cpu", "memory"} <= res.keys(), f"{name(workload, c)} lacks {field}"


def test_every_container_is_locked_down(ours: list[dict]) -> None:
    for workload, pod, c, _ in containers(ours):
        sc, psc = c.get("securityContext", {}), pod.get("securityContext", {})
        label = name(workload, c)
        assert sc.get("runAsNonRoot", psc.get("runAsNonRoot")) is True, label
        assert isinstance(sc.get("runAsUser", psc.get("runAsUser")), int), label
        assert sc.get("allowPrivilegeEscalation") is False, label
        assert sc.get("readOnlyRootFilesystem") is True, label
        assert "ALL" in sc.get("capabilities", {}).get("drop", []), label
        assert not sc.get("privileged"), label
        assert psc.get("seccompProfile", {}).get("type") == "RuntimeDefault", label
        assert not (
            pod.get("hostNetwork") or pod.get("hostPID") or pod.get("hostIPC")
        ), label


def test_long_running_containers_have_probes(ours: list[dict]) -> None:
    # Init containers can't have probes, and a Job's container runs to completion: they're
    # bounded by activeDeadlineSeconds instead (checked below).
    for workload, _, c, is_init in containers(ours):
        if is_init or workload["kind"] in {"Job", "CronJob"}:
            continue
        assert "livenessProbe" in c and "readinessProbe" in c, name(workload, c)


def test_jobs_are_bounded(ours: list[dict]) -> None:
    for d in ours:
        if d["kind"] == "CronJob":
            job = d["spec"]["jobTemplate"]["spec"]
            assert job["activeDeadlineSeconds"] <= 600
            assert d["spec"]["concurrencyPolicy"] == "Forbid"


def test_token_only_where_the_kubernetes_api_is_called(ours: list[dict]) -> None:
    for workload, pod in pod_specs(ours):
        account = pod.get("serviceAccountName", "default")
        assert account != "default", workload["metadata"]["name"]
        assert pod.get("automountServiceAccountToken") is (account in TOKEN_ACCOUNTS), (
            account
        )
    for d in ours:
        if d["kind"] == "ServiceAccount":
            expected = d["metadata"]["name"] in TOKEN_ACCOUNTS
            assert d.get("automountServiceAccountToken") is expected, d["metadata"][
                "name"
            ]


def test_pod_identity_service_accounts_exist(ours: list[dict], config: str) -> None:
    accounts = {
        (d["metadata"]["namespace"], d["metadata"]["name"])
        for d in ours
        if d["kind"] == "ServiceAccount"
    }
    assert POD_IDENTITY_ACCOUNTS <= accounts
    assert (("consoletrack", "canary") in accounts) == (config != "local")


def test_app_charts_are_namespaced_only(app_objects: list[dict]) -> None:
    # CI's deploy role is a namespaced Role; it couldn't apply these anyway.
    scoped = [
        f"{d['kind']}/{d['metadata']['name']}"
        for d in app_objects
        if d["kind"] in CLUSTER_SCOPED_KINDS
    ]
    assert scoped == []
    for d in app_objects:
        assert d["metadata"].get("namespace") in {"consoletrack", "demo"}, d[
            "metadata"
        ]["name"]


def roles_bound_to(
    docs: list[dict], subject_kind: str, subject_name: str
) -> list[dict]:
    bindings = [
        b for b in docs
        if b["kind"] == "RoleBinding"
        and any(s["kind"] == subject_kind and s["name"] == subject_name for s in b["subjects"])
    ]  # fmt: skip
    roles = {
        (r["metadata"]["namespace"], r["metadata"]["name"]): r
        for r in docs
        if r["kind"] == "Role"
    }
    out = []
    for b in bindings:
        assert b["roleRef"]["kind"] == "Role", "bindings refer to namespaced Roles only"
        out.append(roles[(b["metadata"]["namespace"], b["roleRef"]["name"])])
    return out


def test_no_wildcards_in_rbac(ours: list[dict]) -> None:
    for r in (d for d in ours if d["kind"] in {"Role", "ClusterRole"}):
        for rule in r["rules"]:
            for field in ("apiGroups", "resources", "verbs"):
                assert "*" not in rule.get(field, []), (
                    f"{r['metadata']['name']}: {field}"
                )


def test_k8s_mcp_is_read_only_without_secrets(ours: list[dict]) -> None:
    roles = roles_bound_to(ours, "ServiceAccount", "k8s-mcp")
    assert {r["metadata"]["namespace"] for r in roles} == {"demo"}
    for r in roles:
        for rule in r["rules"]:
            assert set(rule["verbs"]) <= {"get", "list", "watch"}
            assert not set(rule["resources"]) & {
                "secrets", "configmaps", "pods/exec", "pods/attach", "pods/portforward", "serviceaccounts/token",
            }  # fmt: skip


def test_remediator_can_only_patch_deployments(ours: list[dict]) -> None:
    roles = roles_bound_to(ours, "ServiceAccount", "remediator")
    assert {r["metadata"]["namespace"] for r in roles} == {"demo"}
    for r in roles:
        grants = {
            (res, verb)
            for rule in r["rules"]
            for res in rule["resources"]
            for verb in rule["verbs"]
        }
        assert grants == {
            ("deployments", "get"), ("deployments", "patch"),
            ("replicasets", "get"), ("replicasets", "list"),
            ("pods", "get"), ("pods", "list"),
        }  # fmt: skip


def test_deployers_are_limited_to_two_namespaces(ours: list[dict]) -> None:
    roles = roles_bound_to(ours, "Group", "consoletrack-deployers")
    assert {r["metadata"]["namespace"] for r in roles} == {"consoletrack", "demo"}
    for r in roles:
        groups = {g for rule in r["rules"] for g in rule["apiGroups"]}
        # No apiextensions, admissionregistration, storage, or node-level access.
        assert groups <= {
            "", "apps", "batch", "rbac.authorization.k8s.io", "postgresql.cnpg.io",
            "monitoring.coreos.com", "gateway.networking.k8s.io", "gateway.k8s.aws",
        }  # fmt: skip
        for rule in r["rules"]:
            # Release records are ConfigMaps (HELM_DRIVER=configmap): no Secret access at all.
            assert "secrets" not in rule["resources"]
            if {"pods", "pods/exec", "nodes"} & set(rule["resources"]):
                assert set(rule["verbs"]) <= {"get", "list", "watch"}


def test_remediation_ships_only_the_oom_bump_in_auto(ours: list[dict]) -> None:
    (cm,) = [
        d
        for d in ours
        if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "remediation-policies"
    ]
    policies = yaml.safe_load(cm["data"]["policies.yaml"])
    assert policies["autoEnabled"] is True
    modes = {p["name"]: p["mode"] for p in policies["policies"]}
    assert modes == {"oom-memory-bump": "auto", "default": "manual"}
    (oom,) = [p for p in policies["policies"] if p["name"] == "oom-memory-bump"]
    # Matched on the remediation kind; the incident's alert is the user-impact one.
    assert oom["match"] == {"kind": "set_memory_limit", "namespace": "demo"}
    assert oom["bounds"] == {"maxMultiplier": 2, "ceiling": "512Mi"}
    assert oom["rateLimit"] == {"perWorkload": 1, "window": "1h"}
    assert (oom["verify"]["rolloutTimeout"], oom["verify"]["waitForResolved"]) == (
        "3m",
        "5m",
    )


def test_backend_and_worker_read_the_policies_and_the_kill_switch(
    ours: list[dict],
) -> None:
    for workload in ("backend", "worker"):
        ((_, _, container, _),) = [
            c
            for c in containers(ours)
            if c[0]["metadata"]["name"] == workload and not c[3]
        ]
        env = {e["name"]: e.get("value") for e in container["env"]}
        assert (
            env["REMEDIATION_POLICIES_FILE"]
            == "/etc/consoletrack/remediation/policies.yaml"
        )
        assert env["AUTOFIX_ENABLED"] == "true"
        assert env["K8S_ALLOWED_NAMESPACES"] == "demo"
        # Only the backend applies.
        assert ("REMEDIATION_APPLY_ENABLED" in env) is (workload == "backend")


def test_worker_offers_aws_mcp_where_it_runs(ours: list[dict], config: str) -> None:
    ((_, _, worker, _),) = [
        c for c in containers(ours) if c[0]["metadata"]["name"] == "worker" and not c[3]
    ]
    env = {e["name"]: e.get("value") for e in worker["env"]}
    assert env["K8S_MCP_URL"] == "http://k8s-mcp:8080/mcp"
    # Where aws-mcp isn't deployed (kind), the agent runs with k8s-mcp only.
    expected = "http://aws-mcp:8080/mcp" if AWS_MCP_ENABLED[config] else ""
    assert env["AWS_MCP_URL"] == expected


def test_exposure(ours: list[dict], config: str) -> None:
    gateways = [d for d in ours if d["kind"] == "Gateway"]
    frontend_cm = [
        d
        for d in ours
        if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "frontend-nginx"
    ]
    if config != "eks-alb":
        # port-forward: nothing public; the frontend's nginx proxies /api itself.
        assert gateways == [] and len(frontend_cm) == 1
        assert not [
            d
            for d in ours
            if d["kind"] == "Service" and d["spec"].get("type") != "ClusterIP"
        ]
        return
    assert frontend_cm == []
    (gw,) = gateways
    listeners = {(lst["protocol"], lst["port"]) for lst in gw["spec"]["listeners"]}
    assert listeners == {("HTTP", 80), ("HTTPS", 443)}
    (lbc,) = [d for d in ours if d["kind"] == "LoadBalancerConfiguration"]
    ranges = lbc["spec"]["sourceRanges"]
    assert ranges and "0.0.0.0/0" not in ranges
    (https,) = lbc["spec"]["listenerConfigurations"]
    assert https["protocolPort"] == "HTTPS:443" and https[
        "defaultCertificate"
    ].startswith("arn:aws:acm:")
    routes = {d["metadata"]["name"]: d for d in ours if d["kind"] == "HTTPRoute"}
    # Only the redirect is on plain HTTP; the app is served over HTTPS only, and /metrics never.
    assert (
        routes["consoletrack-https-redirect"]["spec"]["parentRefs"][0]["sectionName"]
        == "http"
    )
    app = routes["consoletrack"]
    assert app["spec"]["parentRefs"][0]["sectionName"] == "https"
    paths = {
        m["path"]["value"] for rule in app["spec"]["rules"] for m in rule["matches"]
    }
    assert paths == {"/api", "/"}


def observability_workloads(rendered: dict[str, list[dict]], config: str) -> list[dict]:
    """Tempo, Loki, and the collector (third-party charts, so only `helmfile template` has them)."""
    return [
        d
        for d in rendered[config]
        if d["kind"] in WORKLOAD_KINDS and chart_of(d) in OBSERVABILITY_CHARTS
    ]


def test_observability_releases_are_on_eks_only(
    rendered: dict[str, list[dict]], config: str
) -> None:
    if not os.environ.get("HELM_RENDER_DIR"):
        pytest.skip("Tempo, Loki, and the collector render only through helmfile")
    workloads = observability_workloads(rendered, config)
    if config == "local":
        # `make local` runs the same three in Compose.
        assert workloads == []
        return
    assert {chart_of(d) for d in workloads} == set(OBSERVABILITY_CHARTS)
    assert {d["metadata"]["namespace"] for d in workloads} == {"monitoring"}
    assert {d["metadata"]["name"] for d in workloads} == {
        "tempo",
        "loki",
        "otel-collector",
    }


def test_observability_containers_are_locked_down(
    rendered: dict[str, list[dict]], config: str
) -> None:
    if not os.environ.get("HELM_RENDER_DIR"):
        pytest.skip("Tempo, Loki, and the collector render only through helmfile")
    for workload, pod in pod_specs(observability_workloads(rendered, config)):
        for c in pod.get("initContainers", []) + pod["containers"]:
            sc, psc = c.get("securityContext", {}), pod.get("securityContext", {})
            label = name(workload, c)
            assert sc.get("runAsNonRoot", psc.get("runAsNonRoot")) is True, label
            assert isinstance(sc.get("runAsUser", psc.get("runAsUser")), int), label
            assert sc.get("allowPrivilegeEscalation") is False, label
            assert sc.get("readOnlyRootFilesystem") is True, label
            assert "ALL" in sc.get("capabilities", {}).get("drop", []), label
            assert psc.get("seccompProfile", {}).get("type") == "RuntimeDefault", label
            assert not (
                pod.get("hostNetwork") or pod.get("hostPID") or pod.get("hostIPC")
            ), label
            # Requests and a memory limit, like the rest of the monitoring stack (no CPU limits).
            res = c.get("resources", {})
            assert {"cpu", "memory"} <= res.get("requests", {}).keys(), label
            assert "memory" in res.get("limits", {}), label
            assert "livenessProbe" in c and "readinessProbe" in c, label
            # Pinned by digest, and no API token: none of the three calls the Kubernetes API.
            assert "@sha256:" in c["image"], label
        assert pod.get("automountServiceAccountToken") is False, name(workload, c)


def test_observability_leaves_no_cluster_objects_or_volumes_behind(
    rendered: dict[str, list[dict]], config: str
) -> None:
    if not os.environ.get("HELM_RENDER_DIR"):
        pytest.skip("Tempo, Loki, and the collector render only through helmfile")
    names = {"tempo", "loki", "otel-collector"}
    mine = [
        d
        for d in rendered[config]
        if chart_of(d) in OBSERVABILITY_CHARTS or d["metadata"]["name"] in names
    ]
    # emptyDir storage: `make down` has no PVC to remove, and nothing needs cluster-wide RBAC.
    assert [d for d in mine if d["kind"] == "PersistentVolumeClaim"] == []
    for d in mine:
        if d["kind"] == "StatefulSet":
            assert "volumeClaimTemplates" not in d["spec"], d["metadata"]["name"]
    assert [d for d in mine if d["kind"] in {"ClusterRole", "ClusterRoleBinding"}] == []


def test_dashboards_come_from_the_one_json_file(
    rendered: dict[str, list[dict]], config: str
) -> None:
    cms = {
        d["metadata"]["name"]: d
        for d in rendered[config]
        if d["kind"] == "ConfigMap" and chart_of(d) == "grafana-dashboards"
    }
    if config == "local":
        assert cms == {}
        return
    assert set(cms) == {
        "grafana-dashboard-incident-journey",
        "grafana-dashboard-nodes",
        "grafana-dashboard-k8s-resources-pod",
        "grafana-dashboard-alertmanager-overview",
    }
    for cm in cms.values():
        assert cm["metadata"]["labels"]["grafana_dashboard"] == "1"
        assert cm["metadata"]["namespace"] == "monitoring"
        for text in cm["data"].values():
            json.loads(text)
    # Equal to observability/grafana/dashboards/incident-journey.json, not a second copy.
    source = json.loads(INCIDENT_JOURNEY.read_text())
    shipped = json.loads(
        cms["grafana-dashboard-incident-journey"]["data"]["incident-journey.json"]
    )
    assert shipped == source


def test_apps_export_to_the_collector_only_where_it_runs(
    ours: list[dict], config: str
) -> None:
    for workload in ("backend", "worker", "k8s-mcp"):
        ((_, _, container, _),) = [
            c
            for c in containers(ours)
            if c[0]["metadata"]["name"] == workload and not c[3]
        ]
        env = {e["name"]: e.get("value") for e in container["env"]}
        if config == "local":
            # Compose's apps do their own wiring; the chart exports nothing.
            assert "TELEMETRY_EXPORTER" not in env and not any(
                k.startswith("OTEL_") for k in env
            )
            continue
        assert env["TELEMETRY_EXPORTER"] == "otlp", workload
        assert (
            env["OTEL_EXPORTER_OTLP_ENDPOINT"]
            == "http://otel-collector.monitoring.svc.cluster.local:4318"
        ), workload


def test_charts_are_labelled(app_objects: list[dict]) -> None:
    assert {chart_of(d) for d in app_objects} == set(APP_CHARTS)


def test_no_duplicate_keys(config: str) -> None:
    # PyYAML (and so every other test here) keeps the last of two equal keys; the API server's
    # strict decoding and other tools reject the manifest. Rendered with plain `helm template`,
    # our charts only.
    for chart, _, text in helm_template_text(config):
        try:
            list(yaml.load_all(text, Loader=DuplicateKeyLoader))
        except yaml.constructor.ConstructorError as exc:
            pytest.fail(f"{chart} ({config}): {exc}")


@pytest.mark.parametrize(
    ("match", "message"),
    [
        (
            {"kind": "revert_workflow", "namespace": "demo"},
            "changes AWS and can never be auto",
        ),
        (
            {"kind": "codify_pr", "namespace": "demo"},
            "changes AWS and can never be auto",
        ),
        ({}, "auto needs match.kind"),
        ({"kind": "set_memory_limit"}, "auto needs match.namespace"),
    ],
)
def test_the_chart_refuses_unsafe_auto_policies(match: dict, message: str) -> None:
    values = {
        "target": "local",
        "app": {"intakeMode": "replay"},
        "remediation": {
            "autoEnabled": True,
            "policies": [{"name": "unsafe", "match": match, "mode": "auto"}],
        },
    }
    with pytest.raises(subprocess.CalledProcessError) as exc:
        helm_template("consoletrack", "consoletrack", "consoletrack", values)
    assert message in exc.value.stderr


def test_externaldns_does_not_evaluate_target_health(
    rendered: dict[str, list[dict]],
) -> None:
    """The alias ExternalDNS writes must not be health-evaluated: right after `make up` the ALB's
    targets are unhealthy, and Route 53 then answers NOERROR with no addresses, which resolvers cache."""
    flag = "--no-aws-evaluate-target-health"
    values = (HELM_DIR / "values" / "external-dns.yaml.gotmpl").read_text()
    assert flag in values
    # `helmfile template` renders the chart too (not the plain-helm fallback of our own charts).
    deployments = [
        d
        for d in rendered["eks-alb"]
        if d["kind"] == "Deployment" and d["metadata"]["name"] == "external-dns"
    ]
    if os.environ.get("HELM_RENDER_DIR"):
        (deployment,) = deployments
        (container,) = deployment["spec"]["template"]["spec"]["containers"]
        assert flag in container["args"]
        assert "--aws-evaluate-target-health" not in container["args"]
