"""The consoletrack chart's pieces that `make local` applies to kind (scripts/local-kind.sh).

Rendered with compose/kind-values.yaml and the same --show-only templates as the script: only
ServiceAccounts, k8s-mcp's read-only RBAC, the remediator's patch Role, the remediation policies
(the script extracts them for Compose), and an AlertmanagerConfig pointed at the Compose backend.
Nothing that runs a pod, and no Secret (the script creates consoletrack-webhook).
"""

from __future__ import annotations

import shutil
import subprocess
import urllib.parse
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
    "templates/rbac-remediator.yaml",
    "templates/serviceaccounts.yaml",
    "templates/remediation-policies.yaml",
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
    assert kinds == {
        "ServiceAccount",
        "Role",
        "RoleBinding",
        "ConfigMap",
        "AlertmanagerConfig",
    }
    (config_map,) = [d for d in docs if d["kind"] == "ConfigMap"]
    assert config_map["metadata"]["name"] == "remediation-policies"


def test_remediator_patches_demo_only(docs: list[dict]) -> None:
    roles = [
        d for d in docs if d["kind"] == "Role" and d["metadata"]["name"] == "remediator"
    ]
    assert [r["metadata"]["namespace"] for r in roles] == ["demo"]
    (binding,) = [
        d
        for d in docs
        if d["kind"] == "RoleBinding" and d["metadata"]["name"] == "remediator"
    ]
    assert binding["subjects"] == [
        {"kind": "ServiceAccount", "name": "remediator", "namespace": "consoletrack"}
    ]


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


def test_only_user_impact_alerts_open_incidents(docs: list[dict]) -> None:
    """#79: KubePodCrashLooping opened a second incident for one OOM episode. Only rules labelled
    consoletrack_incident="true" reach the backend; everything else stays in Alertmanager."""
    (config,) = [d for d in docs if d["kind"] == "AlertmanagerConfig"]
    route = config["spec"]["route"]
    matchers = {m["name"]: (m["matchType"], m["value"]) for m in route["matchers"]}
    assert matchers["consoletrack_incident"] == ("=", "true")
    assert matchers["namespace"][0] == "=~"
    assert route["groupBy"] == ["namespace", "workload"]


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


# --- the alert-path check in `scripts/local-kind.sh alerts` ------------------------------------

FAKE_KUBECTL = """#!/usr/bin/env bash
# Answers what `alerts` asks: the Alertmanager pod's name, and each `exec` (the wget probe) with
# the next exit code from $PROBE_CODES. Everything else (create, apply) succeeds quietly.
case " $* " in
*" get pods "*) echo pod/alertmanager-kube-prometheus-stack-alertmanager-0 ;;
*" exec "*)
	code=$(head -n 1 "$PROBE_CODES"); sed -i.bak 1d "$PROBE_CODES"
	echo "exec" >> "$PROBE_CALLS"; exit "${code:-0}" ;;
*) cat > /dev/null ;;
esac
"""
FAKE_HELM = "#!/usr/bin/env bash\necho 'kind: AlertmanagerConfig'\n"


def run_alerts(
    tmp_path: Path, codes: list[int]
) -> tuple[subprocess.CompletedProcess[str], int]:
    """Runs the real `alerts` step with fake kubectl and helm; returns the result and the number
    of probe attempts."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("kubectl", FAKE_KUBECTL), ("helm", FAKE_HELM)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    (tmp_path / "codes").write_text("".join(f"{code}\n" for code in codes))
    (tmp_path / "calls").write_text("")
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "PROBE_CODES": str(tmp_path / "codes"),
        "PROBE_CALLS": str(tmp_path / "calls"),
        # The token from the environment, so the script never asks Compose for it.
        "ALERT_WEBHOOK_TOKEN": "test-token",
        "LOCAL_KIND_PROBE_DELAY": "0",
    }
    result = subprocess.run(
        ["bash", str(REPO / "scripts/local-kind.sh"), "alerts"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,  # the tests assert the exit code
    )
    return result, len((tmp_path / "calls").read_text().splitlines())


def test_alerts_check_passes_when_alertmanager_reaches_the_backend(
    tmp_path: Path,
) -> None:
    result, attempts = run_alerts(tmp_path, [0])
    assert result.returncode == 0, result.stderr
    assert attempts == 1
    assert "Alertmanager reaches the Compose backend" in result.stdout


def test_alerts_check_retries_a_crashed_wget(tmp_path: Path) -> None:
    """busybox wget in the Alertmanager image sometimes segfaults (exit 139) on a working path."""
    result, attempts = run_alerts(tmp_path, [139, 0])
    assert result.returncode == 0, result.stderr
    assert attempts == 2
    assert "crashed (exit 139); retrying" in result.stderr


def test_alerts_check_warns_but_goes_on_when_every_attempt_crashed(
    tmp_path: Path,
) -> None:
    """A crash says nothing about the network, so make local carries on (and prints its URLs
    and passwords) with a warning and the manual check."""
    result, attempts = run_alerts(tmp_path, [139] * 5)
    assert result.returncode == 0, result.stderr
    assert attempts == 5
    assert "warning: couldn't check the alert path" in result.stderr
    assert "alerts/webhook" in result.stderr


def test_alerts_check_fails_when_alertmanager_cant_reach_the_backend(
    tmp_path: Path,
) -> None:
    result, attempts = run_alerts(tmp_path, [1, 139, 1, 1, 1])
    assert result.returncode == 1
    assert attempts == 5
    assert (
        "Alertmanager can't reach http://consoletrack-backend:8000 (exit 1)"
        in result.stderr
    )


# --- `scripts/local-kind.sh measure` (make local-measure, #79) ---------------------------------

MEASURE_KUBECTL = """#!/usr/bin/env bash
echo "kubectl $*" >> "$CALLS"
case " $* " in
*" get --raw "*)
	# 150 MiB for every query, or nothing for the cache gauge when NO_CACHE is set.
	case "$*" in *demo_app_report_cache_bytes*) [ -n "${NO_CACHE:-}" ] && { echo '{"data":{"result":[]}}'; exit 0; } ;; esac
	echo '{"data":{"result":[{"value":[0,"157286400"]}]}}' ;;
*" rollout status "*) [ -z "${FAIL_ROLLOUT:-}" ] || exit 1 ;;
esac
"""
RECORDER = '#!/usr/bin/env bash\necho "$(basename "$0") $*" >> "$CALLS"\n'


def run_measure(
    tmp_path: Path, **env: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (
        ("kubectl", MEASURE_KUBECTL),
        ("helmfile", RECORDER),
        ("docker", RECORDER),
    ):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    calls = tmp_path / "calls"
    calls.write_text("")
    result = subprocess.run(
        ["bash", str(REPO / "scripts/local-kind.sh"), "measure"],
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "CALLS": str(calls),
            "HELM_DRIVER": "configmap",
            "MEASURE_MINUTES": "2",
            "MEASURE_INTERVAL": "0",
            **env,
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,  # the tests assert the exit code
    )
    return result, calls.read_text().splitlines()


@pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq")
def test_measure_samples_then_puts_everything_back(tmp_path: Path) -> None:
    result, calls = run_measure(tmp_path)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "baseline, no fault: working set 150 MiB" in out
    assert "minute 2: working set 150 MiB, RSS 150 MiB, report cache 150 MiB" in out
    assert "working set  150 MiB" in out and "report cache 150 MiB" in out
    # In order: worker stopped, fault on, limit raised ... fault off, worker started.
    order = [
        c
        for c in calls
        if not c.startswith("kubectl --context kind-consoletrack get --raw")
    ]
    assert "stop worker" in order[0]
    assert order[1].startswith("helmfile ") and "demoFault=oom" in order[1]
    assert (
        "set resources deployment/demo-app -c demo-app --limits=memory=512Mi"
        in order[2]
    )
    assert order[-2].startswith("helmfile ") and "demoFault" not in order[-2]
    assert "start worker" in order[-1]
    # The queries go through the API server's proxy to Prometheus, URL-encoded.
    raw = [c for c in calls if " get --raw " in c]
    assert all(
        "/services/kube-prometheus-stack-prometheus:9090/proxy/api/v1/query?query=" in c
        for c in raw
    )
    queries = [urllib.parse.unquote(c.split("query=", 1)[1]) for c in raw]
    assert (
        'max(container_memory_working_set_bytes{namespace="demo",container="demo-app"})'
        in queries
    )
    # A peak is max over pods of each pod's max over time: valid PromQL, not max(...)[5m].
    assert (
        'max(max_over_time(container_memory_working_set_bytes{namespace="demo",container="demo-app"}[5m]))'
        in queries
    )
    assert (
        'max(max_over_time(demo_app_report_cache_bytes{namespace="demo"}[5m]))'
        in queries
    )


@pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq")
def test_measure_puts_everything_back_when_it_fails(tmp_path: Path) -> None:
    result, calls = run_measure(tmp_path, FAIL_ROLLOUT="1")
    assert result.returncode != 0
    assert calls[-2].startswith("helmfile ") and "demoFault" not in calls[-2]
    assert "start worker" in calls[-1]


@pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq")
def test_measure_says_when_a_series_is_missing(tmp_path: Path) -> None:
    result, _ = run_measure(tmp_path, NO_CACHE="1")
    assert result.returncode == 0, result.stderr
    assert "report cache no data" in result.stdout
