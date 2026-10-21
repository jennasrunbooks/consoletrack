"""docker-compose.yaml invariants that `docker compose config` doesn't check.

Runs without Docker (`make test-compose`, part of `make test`); `make lint-compose` validates
the file itself with `docker compose config` wherever Docker is installed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yaml"
SERVICES = yaml.safe_load(COMPOSE.read_text())["services"]
# `make local`'s additions for the kind cluster (docker compose -f ... -f compose/kind.yaml).
KIND = COMPOSE.parent / "compose" / "kind.yaml"
KIND_SERVICES = yaml.safe_load(KIND.read_text())["services"]
# Names that would hold a real credential: each must come from .env or the shell.
SECRET_NAME = re.compile(r"SECRET|TOKEN|PASSWORD|API_KEY|HASH")
# The only literal credential: Postgres inside Compose, also in .env.example's DATABASE_URL.
LOCAL_ONLY = {("postgres", "POSTGRES_PASSWORD")}
ACCOUNT_ID = re.compile(r"\b\d{12}\b")
OBSERVABILITY = COMPOSE.parent / "observability"
# What compose/kind.yaml adds for the observability stack: kind's network, so the collector and
# Grafana reach Prometheus's NodePort, and the collector's metrics config.
OBSERVABILITY_ON_KIND = {"otel-collector", "grafana"}


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_ports_bind_to_loopback_only(name: str) -> None:
    for port in SERVICES[name].get("ports", []):
        assert isinstance(port, str) and port.startswith("127.0.0.1:"), (name, port)


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_secrets_are_never_literals(name: str) -> None:
    for key, value in (SERVICES[name].get("environment") or {}).items():
        if SECRET_NAME.search(key) and (name, key) not in LOCAL_ONLY:
            assert str(value) == f"${{{key}:-}}", (name, key)


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_images_are_pinned_or_built(name: str) -> None:
    service = SERVICES[name]
    if "build" in service:
        assert service["image"].endswith(":local"), name
    else:
        assert re.search(r":[\w.-]+@sha256:[0-9a-f]{64}$", service["image"]), name


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_dependencies_wait_for_health(name: str) -> None:
    depends_on = SERVICES[name].get("depends_on", {})
    assert isinstance(depends_on, dict), name
    for dependency, condition in depends_on.items():
        assert condition == {"condition": "service_healthy"}, (name, dependency)
        assert "healthcheck" in SERVICES[dependency], dependency


def test_kind_override_adds_only_networks_and_the_remediation_wiring() -> None:
    """compose/kind.yaml publishes nothing. It joins the backend to kind's network and gives the
    backend and worker the remediation settings and the files scripts/local-kind.sh writes,
    read-only: the remediator kubeconfig (backend only) and the policies."""
    assert set(KIND_SERVICES) == {"backend", "worker", *OBSERVABILITY_ON_KIND}
    backend, worker = KIND_SERVICES["backend"], KIND_SERVICES["worker"]
    assert set(backend) == {"networks", "environment", "volumes"}
    assert set(worker) == {"environment", "volumes"}
    networks = backend["networks"]
    assert set(networks) == {"default", "kind"}
    # The name Alertmanager posts to (compose/kind-values.yaml → monitoring.webhookUrl).
    assert networks["kind"]["aliases"] == ["consoletrack-backend"]
    policies = "./kind-remediation-policies.yaml:/etc/consoletrack/remediation/policies.yaml:ro"
    assert backend["volumes"] == [
        "./kind-remediator.kubeconfig:/run/kube/config:ro",
        policies,
    ]
    # The worker decides; only the backend can reach the cluster.
    assert worker["volumes"] == [policies]
    assert "KUBECONFIG" not in worker["environment"]
    for service in (backend, worker):
        assert "ports" not in service
        assert service["environment"]["AUTOFIX_ENABLED"] == "${AUTOFIX_ENABLED:-true}"
        for key in service["environment"]:
            assert not SECRET_NAME.search(key), key


def test_kind_services_use_the_external_kind_network() -> None:
    networks = yaml.safe_load(COMPOSE.read_text())["networks"]
    assert networks["kind"] == {"external": True}
    for name, service in SERVICES.items():
        if "kind" in service.get("networks", []):
            assert service.get("profiles") == ["kind"], name


def test_no_account_ids() -> None:
    compose_dir = COMPOSE.parent.joinpath("compose")
    for path in [COMPOSE, *compose_dir.glob("*.conf"), *compose_dir.glob("*.yaml")]:
        found = set(ACCOUNT_ID.findall(path.read_text())) - {"123456789012"}
        assert not found, path


def test_kind_override_sends_metrics_to_kinds_prometheus() -> None:
    collector, grafana = KIND_SERVICES["otel-collector"], KIND_SERVICES["grafana"]
    assert set(collector) == {"command", "networks"}
    assert collector["command"] == [
        "--config=/etc/otelcol/config.yaml",
        "--config=/etc/otelcol/kind.yaml",
    ]
    assert grafana == {"networks": ["default", "kind"]}
    assert collector["networks"] == ["default", "kind"]
    base = yaml.safe_load((OBSERVABILITY / "otel-collector/config.yaml").read_text())
    kind = yaml.safe_load((OBSERVABILITY / "otel-collector/kind.yaml").read_text())
    # Plain Compose has no Prometheus: metrics stop at the collector.
    assert base["service"]["pipelines"]["metrics"]["exporters"] == ["debug"]
    assert kind["service"]["pipelines"]["metrics"]["exporters"] == [
        "prometheus_remote_write"
    ]
    remote_write = kind["exporters"]["prometheus_remote_write"]
    assert (
        remote_write["endpoint"]
        == "http://consoletrack-control-plane:30090/api/v1/write"
    )


def test_observability_mounts_exist_read_only() -> None:
    for name in ("otel-collector", "tempo", "loki", "grafana"):
        for volume in SERVICES[name].get("volumes", []):
            source, _target, *mode = volume.split(":")
            if source.startswith("./"):
                assert (COMPOSE.parent / source).exists(), (name, source)
                assert mode == ["ro"], (name, volume)


def test_apps_export_otlp_to_the_collector() -> None:
    for name in ("backend", "worker", "k8s-mcp"):
        env = SERVICES[name]["environment"]
        assert env["TELEMETRY_EXPORTER"] == "${TELEMETRY_EXPORTER:-otlp}", name
        assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://otel-collector:4318", name


def test_grafana_never_uses_the_default_admin_password() -> None:
    env = SERVICES["grafana"]["environment"]
    assert "GF_SECURITY_ADMIN_PASSWORD" not in env
    assert env["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    # Unset GRAFANA_ADMIN_PASSWORD means a generated one, never Grafana's admin/admin default.
    entrypoint = (OBSERVABILITY / "grafana/entrypoint.sh").read_text()
    assert "/dev/urandom" in entrypoint
    assert "export GF_SECURITY_ADMIN_PASSWORD" in entrypoint


def test_the_incident_journey_dashboard_uses_the_provisioned_datasources() -> None:
    dashboard = json.loads(
        (OBSERVABILITY / "grafana/dashboards/incident-journey.json").read_text()
    )
    datasources = yaml.safe_load(
        (
            OBSERVABILITY / "grafana/provisioning/datasources/datasources.yaml"
        ).read_text()
    )["datasources"]
    uids = {ds["uid"] for ds in datasources}
    assert uids == {"prometheus", "tempo", "loki"}
    for panel in dashboard["panels"]:
        if panel["type"] != "row":
            assert panel["datasource"]["uid"] in uids, panel["title"]
    tempo = next(ds for ds in datasources if ds["uid"] == "tempo")
    assert tempo["jsonData"]["tracesToLogsV2"]["datasourceUid"] == "loki"
