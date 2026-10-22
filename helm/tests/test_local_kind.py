"""The consoletrack chart's pieces that `make local` applies to kind (scripts/local-kind.sh).

Rendered with compose/kind-values.yaml and the same --show-only templates as the script: only
ServiceAccounts, k8s-mcp's read-only RBAC, the remediator's patch Role, the remediation policies
(the script extracts them for Compose), and an AlertmanagerConfig pointed at the Compose backend.
Nothing that runs a pod, and no Secret (the script creates consoletrack-webhook).
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
