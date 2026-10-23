"""The consoletrack chart's pieces that `make local` applies to kind (scripts/local-kind.sh).

Rendered with compose/kind-values.yaml and the same --show-only templates as the script: only
ServiceAccounts, k8s-mcp's read-only RBAC, the remediator's patch Role, the remediation policies
(the script extracts them for Compose), and an AlertmanagerConfig pointed at the Compose backend.
Nothing that runs a pod, and no Secret (the script creates consoletrack-webhook).
"""

from __future__ import annotations

import json
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


# --- `scripts/local-kind.sh measure` (make local-measure, #79) ---------------------------------

MEASURE_KUBECTL = """#!/usr/bin/env bash
echo "kubectl $*" >> "$CALLS"
case " $* " in
*" port-forward "*)
	# Like kubectl: announce the local port, then hold the forward open until killed.
	[ -z "${FAIL_FORWARD:-}" ] || { echo "error: unable to forward port"; exit 1; }
	echo "Forwarding from 127.0.0.1:19090 -> 9090"
	echo $$ > "$CALLS.forward-pid"
	exec sleep 60 ;;
*" get deployment demo-app "*) echo -n ghcr.io/jennasrunbooks/consoletrack-demo-app:abc1234 ;;
*" rollout status "*) [ -z "${FAIL_ROLLOUT:-}" ] || exit 1 ;;
esac
"""
# Prometheus behind the port-forward: 150 MiB for every query (1 for the reachability check),
# or nothing for the cache gauge when NO_CACHE is set. Records each query as sent.
PROMETHEUS_CURL = r"""#!/usr/bin/env bash
q=""
for a in "$@"; do case "$a" in query=*) q=${a#query=} ;; esac; done
echo "curl ${*: -1} $q" >> "$CALLS"
case "$q" in
"vector(1)") v=1 ;;
*demo_app_report_cache_bytes*) [ -n "${NO_CACHE:-}" ] && { echo '{"data":{"result":[]}}'; exit 0; }; v=157286400 ;;
*) v=157286400 ;;
esac
echo "{\"data\":{\"result\":[{\"value\":[0,\"$v\"]}]}}"
"""
RECORDER = '#!/usr/bin/env bash\necho "$(basename "$0") $*" >> "$CALLS"\n'
# helmfile: records its calls; with FAIL_SECOND_SYNC set, the second sync (the restore) fails.
FAKE_HELMFILE = (
    RECORDER
    + 'n=$(grep -c "^helmfile " "$CALLS")\n'
    + '[ -n "${FAIL_SECOND_SYNC:-}" ] && [ "$n" -ge 2 ] && exit 1\nexit 0\n'
)


def run_measure(
    tmp_path: Path, **env: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (
        ("kubectl", MEASURE_KUBECTL),
        ("curl", PROMETHEUS_CURL),
        ("helmfile", FAKE_HELMFILE),
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
    # In order: Prometheus forwarded, worker stopped, fault on, limit raised ... fault off,
    # worker started.
    assert " port-forward svc/kube-prometheus-stack-prometheus :9090" in calls[0]
    order = [c for c in calls if not c.startswith("curl ")][1:]
    assert "stop worker" in order[0]
    assert order[1].startswith("helmfile ") and "demoFault=oom" in order[1]
    # The limit goes through Helm too: a kubectl change would conflict with the restoring sync.
    assert "--state-values-set demoMemoryLimit=512Mi" in order[1]
    assert not any(" set resources " in c for c in calls)
    # Recreated rather than synced, so the measurement's revisions don't stay in the rollout
    # history for the next triage to read as a lowered limit.
    assert "-n demo delete deployment demo-app" in order[-3]
    assert order[-2].startswith("helmfile ") and "demoFault" not in order[-2]
    assert "start worker" in order[-1]
    # Queries go to Prometheus through the port-forward, not the API server's service proxy,
    # which the EKS control plane can't use to reach port 9090.
    queries = [c for c in calls if c.startswith("curl ")]
    assert all(
        c.startswith("curl http://127.0.0.1:19090/api/v1/query ") for c in queries
    )
    sent = [c.split(" ", 2)[2] for c in queries]
    assert sent[0] == "vector(1)"  # the reachability check, before anything changes
    assert (
        'max(container_memory_working_set_bytes{namespace="demo",container="demo-app"})'
        in sent
    )
    # A peak is max over pods of each pod's max over time: valid PromQL, not max(...)[5m].
    assert (
        'max(max_over_time(container_memory_working_set_bytes{namespace="demo",container="demo-app"}[5m]))'
        in sent
    )
    assert (
        'max(max_over_time(demo_app_report_cache_bytes{namespace="demo"}[5m]))' in sent
    )
    # The port-forward is stopped, not left running after the script exits (or a zombie, which
    # holds nothing).
    status = Path(
        f"/proc/{(tmp_path / 'calls.forward-pid').read_text().strip()}/status"
    )
    assert not status.exists() or "zombie" in status.read_text()


@pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq")
def test_measure_stops_before_changing_anything_when_prometheus_is_unreachable(
    tmp_path: Path,
) -> None:
    """The first EKS run sampled empty values for minutes: the service proxy couldn't reach
    Prometheus. Now the check comes first and nothing is touched."""
    result, calls = run_measure(tmp_path, FAIL_FORWARD="1")
    assert result.returncode == 1
    assert "couldn't port-forward to Prometheus" in result.stderr
    assert not any(c.startswith(("helmfile ", "docker ")) for c in calls)


@pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq")
def test_measure_puts_everything_back_when_it_fails(tmp_path: Path) -> None:
    result, calls = run_measure(tmp_path, FAIL_ROLLOUT="1")
    assert result.returncode != 0
    assert calls[-2].startswith("helmfile ") and "demoFault" not in calls[-2]
    assert "start worker" in calls[-1]


@pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq")
def test_measure_starts_the_worker_even_if_the_restoring_sync_fails(
    tmp_path: Path,
) -> None:
    """The first real run hit a Helm conflict while putting the fault back, and the exit trap
    stopped there, leaving the worker stopped."""
    result, calls = run_measure(tmp_path, FAIL_SECOND_SYNC="1")
    assert result.returncode == 1
    assert "start worker" in calls[-1]
    assert "couldn't put everything back: recreating demo-app failed" in result.stderr


@pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq")
def test_measure_on_eks_keeps_the_running_image_and_leaves_the_worker_alone(
    tmp_path: Path,
) -> None:
    """`make measure`: an IMAGE_TAG from this checkout might not exist in GHCR, so every sync
    reuses the tag demo-app runs; there's no Compose worker to stop."""
    result, calls = run_measure(
        tmp_path, MEASURE_TARGET="eks", KUBE_CONTEXT="consoletrack-eks"
    )
    assert result.returncode == 0, result.stderr
    assert "measuring on consoletrack-eks, demo-app image tag abc1234" in result.stdout
    syncs = [c for c in calls if c.startswith("helmfile ")]
    assert len(syncs) == 2
    for sync in syncs:
        assert "-e eks --kube-context consoletrack-eks" in sync
        assert "--state-values-set imageTag=abc1234" in sync
    assert "demoMemoryLimit=512Mi" in syncs[0] and "demoFault" not in syncs[1]
    assert not any(c.startswith("docker ") for c in calls)
    assert all(
        "--context consoletrack-eks" in c for c in calls if c.startswith("kubectl ")
    )


@pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq")
def test_measure_says_when_a_series_is_missing(tmp_path: Path) -> None:
    result, _ = run_measure(tmp_path, NO_CACHE="1")
    assert result.returncode == 0, result.stderr
    assert "report cache no data" in result.stdout


# --- make local-oom-run: `oom-prepare` and `oom-run` -------------------------------------------

OOM_KUBECTL = r"""#!/usr/bin/env bash
echo "kubectl $*" >> "$CALLS"
pop() { local first; first=$(head -n 1 "$1"); [ "$(wc -l < "$1")" -gt 1 ] && sed -i.bak 1d "$1"; echo "$first"; }
case " $* " in
*" get deployment demo-app "*) echo -n 1 ;;
*" rollout history "*) printf 'deployment.apps/demo-app\nREVISION  CHANGE-CAUSE\n1         <none>\n' ;;
*" get pods "*) echo pod/alertmanager-kube-prometheus-stack-alertmanager-0 ;;
*" amtool "*) pop "$ALERTS" | tr '|' '\n' ;;
*" port-forward "*) echo "Forwarding from 127.0.0.1:19090 -> 9090"; exec sleep 60 ;;
esac
exit 0
"""
# Answers the API from sequence files: one line per call, the last line repeating.
OOM_CURL = r"""#!/usr/bin/env bash
pop() { local first; first=$(head -n 1 "$1"); [ "$(wc -l < "$1")" -gt 1 ] && sed -i.bak 1d "$1"; echo "$first"; }
url=${*: -1}
case "$url" in
*/api/v1/query)
	# Prometheus: 1 for the reachability check, 0 errors otherwise.
	case "$*" in *"query=vector(1)"*) v=1 ;; *) v=0 ;; esac
	echo "{\"data\":{\"result\":[{\"value\":[0,\"$v\"]}]}}" ;;
*/auth/login) cat > /dev/null; exit 0 ;;
*/api/v1/incidents\?*) pop "$LISTS" ;;
*/api/v1/incidents/*) pop "$DETAILS" ;;
esac
"""
OOM_DOCKER = r"""#!/usr/bin/env bash
echo "docker $*" >> "$CALLS"
case "$*" in *"cat /run/consoletrack/admin-password"*) echo test-password ;; esac
exit 0
"""


def incident(status: str, remediation: dict | None = None) -> str:
    return json.dumps(
        {
            "id": 1,
            "alertname": "DemoAppHighErrorRate",
            "status": status,
            "analysis": None
            if status == "triaging"
            else {
                "confidence": "high",
                "agent_run": {"prompt_version": "v4", "tool_calls": 7},
            },
            "remediation": remediation,
        }
    )


def fix(
    status: str, mode: str = "auto", kind: str = "set_memory_limit", **extra: object
) -> dict:
    steps = [
        {"step": "verify", "ok": status == "succeeded", "detail": "error ratio 0.000"}
    ]
    return {
        "kind": kind,
        "status": status,
        "mode": mode,
        "gate_reason": "policy oom-memory-bump applies it",
        "payload": {"memory_limit": "256Mi"},
        "steps": steps if status in ("succeeded", "failed") else [],
        **extra,
    }


EMPTY = json.dumps({"total": 0, "items": []})
ONE = json.dumps(
    {"total": 1, "items": [{"id": 1, "alertname": "DemoAppHighErrorRate"}]}
)


def run_oom(
    tmp_path: Path,
    step: str,
    *,
    lists: list[str] | None = None,
    details: list[str] | None = None,
    alerts: list[str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (
        ("kubectl", OOM_KUBECTL),
        ("curl", OOM_CURL),
        ("docker", OOM_DOCKER),
        ("helmfile", FAKE_HELMFILE),
    ):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    files = {
        "LISTS": "\n".join(lists or [EMPTY]),
        "DETAILS": "\n".join(details or ["{}"]),
        "ALERTS": "\n".join(alerts or ["Watchdog"]),
        "CALLS": "",
    }
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HELM_DRIVER": "configmap",
        "OOM_RUN_POLL": "0",
    }
    env |= {"OOM_QUIET_POLL": "1", "OOM_QUIET_TIMEOUT": "30"}
    for name, text in files.items():
        (tmp_path / name).write_text(text + "\n" if text else "")
        env[name] = str(tmp_path / name)
    result = subprocess.run(
        ["bash", str(REPO / "scripts/local-kind.sh"), step],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,  # the tests assert the exit code
    )
    return result, (tmp_path / "CALLS").read_text().splitlines()


needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq")


@needs_jq
def test_prepare_recreates_demo_app(tmp_path: Path) -> None:
    """Always: a measurement's 512Mi revision made the agent propose a rollback, and after a fix
    the backend's patch owns the memory limit, so Helm's own sync to turn the fault off fails
    with a server-side apply conflict (run 1, #79)."""
    result, calls = run_oom(tmp_path, "oom-prepare")
    assert result.returncode == 0, result.stderr
    delete = next(
        i for i, c in enumerate(calls) if "-n demo delete deployment demo-app" in c
    )
    sync = next(i for i, c in enumerate(calls) if c.startswith("helmfile "))
    assert delete < sync and "demoFault" not in calls[sync]
    assert "demo-app recreated" in result.stdout
    assert "ready for the run" in result.stdout


@needs_jq
def test_prepare_waits_for_the_alert_to_clear(tmp_path: Path) -> None:
    """A database reset while the alert still fires: the new backend never hears of it."""
    firing = "Watchdog|DemoAppHighErrorRate"
    result, calls = run_oom(
        tmp_path, "oom-prepare", alerts=[firing, firing, "Watchdog"]
    )
    assert result.returncode == 0, result.stderr
    assert sum(" amtool " in c for c in calls) == 3


@needs_jq
def test_run_passes_when_the_auto_fix_succeeds(tmp_path: Path) -> None:
    details = [
        incident("triaging"),
        incident("remediating", fix("running")),
        incident("remediating", fix("succeeded")),
    ]
    result, calls = run_oom(
        tmp_path, "oom-run", lists=[EMPTY, EMPTY, ONE], details=details
    )
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "incident 1: triaging | no proposal" in out
    assert "incident 1: remediating | set_memory_limit succeeded auto | verify" in out
    assert "proposal: set_memory_limit 256Mi" in out
    assert "RESULT: PASS" in out
    syncs = [c for c in calls if c.startswith("helmfile ")]
    # Fault off at the end, recreated rather than synced: the fix's patch owns the limit.
    assert "demoFault=oom" in syncs[0] and "demoFault" not in syncs[-1]
    assert "-n demo delete deployment demo-app" in calls[-2]


@needs_jq
def test_run_asks_for_approval_and_waits(tmp_path: Path) -> None:
    proposed = fix(
        "proposed", mode="manual", gate_reason="the agent's confidence is medium"
    )
    details = [
        incident("awaiting_review", proposed),
        incident("remediating", fix("succeeded", mode="manual", decided_by="admin")),
    ]
    result, _ = run_oom(tmp_path, "oom-run", lists=[EMPTY, ONE], details=details)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "the gate sent it to a human: the agent's confidence is medium" in result.stdout
    )
    assert "approve it at http://localhost:8080/incidents/1" in result.stdout
    assert "approved by admin" in result.stdout
    assert "RESULT: PASS" in result.stdout


@needs_jq
def test_run_fails_on_a_rollback_proposal_and_turns_the_fault_off(
    tmp_path: Path,
) -> None:
    rollback = fix("proposed", mode="manual", kind="rollback")
    result, calls = run_oom(
        tmp_path,
        "oom-run",
        lists=[EMPTY, ONE],
        details=[incident("awaiting_review", rollback)],
    )
    assert result.returncode == 1
    assert "proposal: rollback" in result.stdout
    assert "RESULT: FAIL" in result.stdout
    assert "demoFault" not in [c for c in calls if c.startswith("helmfile ")][-1]


@needs_jq
def test_run_refuses_a_database_with_incidents(tmp_path: Path) -> None:
    result, calls = run_oom(tmp_path, "oom-run", lists=[ONE])
    assert result.returncode == 1
    assert "already has incidents" in result.stderr
    assert not any(c.startswith("helmfile ") for c in calls)  # the fault never went on
