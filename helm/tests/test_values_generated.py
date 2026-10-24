"""`make helm-values` (scripts/helm-values.sh): the generated keys are the ones Helmfile reads.

The script writes helm/environments/values.generated.yaml, layered over eks.yaml. A key it writes
that nothing reads, or that eks.yaml doesn't declare, would be silently ignored on a real
deploy, and a key Helmfile needs but the script never writes would fail only on the cluster. The
tests run the script against recorded `terraform output -json` shapes (no Terraform, no AWS).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "helm-values.sh"
ENVIRONMENTS = REPO / "helm" / "environments"
PLACEHOLDERS = yaml.safe_load(
    (REPO / "helm" / "tests" / "values" / "eks.yaml").read_text()
)

pytestmark = pytest.mark.skipif(
    shutil.which("jq") is None, reason="scripts/helm-values.sh needs jq"
)

# terraform/consoletrack/us-east-1/outputs.tf: output name -> the Helmfile key it feeds.
REGION_OUTPUTS = {
    "cluster_name": "clusterName",
    "vpc_id": "vpcId",
    "changes_queue_url": "changesQueueUrl",
    "changes_dlq_url": "changesDlqUrl",
    "trail_name": "trailName",
    "canary_security_group_id": "canarySecurityGroupId",
}
ENV_SETTINGS = {
    "EXPOSURE": "alb",
    "DOMAIN": "consoletrack.example.com",
    "ALLOWED_CIDRS": "203.0.113.0/24, 198.51.100.7/32",
    "NAME_SUFFIX": "test",
    "AWS_REGION": "us-east-1",
    "WATCHED_IAM_PATHS": "/demo-app/",
    "TRIAGE_MODEL": "claude-haiku-5-5",
    "TRIAGE_MODEL_ESCALATION": "claude-sonnet-5-5",
    "GRAFANA_URL": "http://localhost:3000/d/consoletrack-intake",
}
CERTIFICATE_ARN = "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000"


def tf_json(outputs: dict[str, str]) -> str:
    # `terraform output -json` wraps every value; sensitive ones are still plain with -json.
    return json.dumps(
        {
            k: {"sensitive": True, "type": "string", "value": v}
            for k, v in outputs.items()
        }
    )


@pytest.fixture
def outputs(tmp_path: Path) -> dict[str, Path]:
    region = {out: PLACEHOLDERS[key] for out, key in REGION_OUTPUTS.items()}
    region["certificate_arn"] = CERTIFICATE_ARN
    (tmp_path / "region.json").write_text(tf_json(region))
    (tmp_path / "demo.json").write_text(
        tf_json({"bucket_name": PLACEHOLDERS["demoBucket"]})
    )
    return {"region": tmp_path / "region.json", "demo": tmp_path / "demo.json"}


def run(
    tmp_path: Path, env: dict[str, str], source: str = "terraform"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT)],
        env={
            "PATH": os.environ["PATH"],
            "HELM_VALUES_OUT": str(tmp_path / "values.generated.yaml"),
            "HELM_VALUES_SOURCE": source,
            **env,
        },
        capture_output=True,
        text=True,
        check=False,
    )


def generated(tmp_path: Path) -> dict:
    return yaml.safe_load((tmp_path / "values.generated.yaml").read_text())


def terraform_env(outputs: dict[str, Path]) -> dict[str, str]:
    return {
        **ENV_SETTINGS,
        "HELM_VALUES_REGION_OUTPUTS": str(outputs["region"]),
        "HELM_VALUES_DEMO_OUTPUTS": str(outputs["demo"]),
    }


def test_writes_the_placeholder_keys_with_the_placeholder_values(
    tmp_path: Path, outputs: dict[str, Path]
) -> None:
    result = run(tmp_path, terraform_env(outputs))
    assert result.returncode == 0, result.stderr
    values = generated(tmp_path)
    # Every key helm/tests/values/eks.yaml has (what `make helm-lint` renders), same value.
    assert {k: values[k] for k in PLACEHOLDERS} == PLACEHOLDERS
    assert values["certificateArn"] == CERTIFICATE_ARN
    assert values["allowedCidrs"] == ["203.0.113.0/24", "198.51.100.7/32"]
    assert values["exposure"] == "alb"
    assert values["domain"] == "consoletrack.example.com"


def test_every_generated_key_is_declared_and_read() -> None:
    """Static: eks.yaml and local.yaml declare each key the script can write, Helmfile reads it."""
    script = SCRIPT.read_text()
    written = set(re.findall(r"^\s*add (\w+) ", script, re.MULTILINE)) | {
        "allowedCidrs"
    }
    declared = set(yaml.safe_load((ENVIRONMENTS / "eks.yaml").read_text()))
    assert written <= declared, f"not in eks.yaml: {sorted(written - declared)}"
    assert written <= set(yaml.safe_load((ENVIRONMENTS / "local.yaml").read_text()))
    # What Helmfile and the third-party values templates read from the environment values.
    read = set()
    for f in [
        REPO / "helm" / "helmfile.yaml.gotmpl",
        *(REPO / "helm" / "values").glob("*.gotmpl"),
    ]:
        read |= set(re.findall(r"\.Values\.(\w+)", f.read_text()))
    assert written <= read, f"written but never read: {sorted(written - read)}"
    # And the other way: every Terraform-derived key Helmfile reads is written (a key left out
    # would silently fall back to the empty default in eks.yaml).
    terraform_keys = {
        "clusterName",
        "vpcId",
        "certificateArn",
        "changesQueueUrl",
        "changesDlqUrl",
        "trailName",
        "canarySecurityGroupId",
        "demoBucket",
    }
    assert terraform_keys <= written
    assert terraform_keys <= read


def test_the_file_is_private_and_holds_no_secret(
    tmp_path: Path, outputs: dict[str, Path]
) -> None:
    env = {**terraform_env(outputs), "ANTHROPIC_API_KEY": "sk-ant-test-key"}
    env["ADMIN_PASSWORD_HASH"] = "$argon2id$v=19$hash"
    env["SESSION_SECRET"] = "session-secret-value"
    assert run(tmp_path, env).returncode == 0
    path = tmp_path / "values.generated.yaml"
    text = path.read_text()
    for secret in ("sk-ant-test-key", "argon2id", "session-secret-value"):
        assert secret not in text
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_empty_settings_are_left_out_so_the_defaults_apply(
    tmp_path: Path, outputs: dict[str, Path]
) -> None:
    env = {"AWS_REGION": "us-east-1", "NAME_SUFFIX": "test", "EXPOSURE": "port-forward"}
    env["HELM_VALUES_REGION_OUTPUTS"] = str(outputs["region"])
    assert run(tmp_path, env).returncode == 0
    values = generated(tmp_path)
    assert "domain" not in values
    assert "allowedCidrs" not in values
    assert (
        "demoBucket" not in values
    )  # no demo-app root yet: the demo app reads no S3 object
    assert values["exposure"] == "port-forward"


def test_certificate_is_omitted_when_terraform_has_none(
    tmp_path: Path, outputs: dict[str, Path]
) -> None:
    region = json.loads(outputs["region"].read_text())
    region["certificate_arn"]["value"] = None
    outputs["region"].write_text(json.dumps(region))
    assert run(tmp_path, terraform_env(outputs)).returncode == 0
    assert "certificateArn" not in generated(tmp_path)


def test_alb_without_allowed_cidrs_fails(
    tmp_path: Path, outputs: dict[str, Path]
) -> None:
    env = {**terraform_env(outputs), "ALLOWED_CIDRS": ""}
    result = run(tmp_path, env)
    assert result.returncode == 1
    assert "ALLOWED_CIDRS" in result.stderr
    assert not (tmp_path / "values.generated.yaml").exists()


def test_env_source_is_for_ci(tmp_path: Path) -> None:
    env = {
        **ENV_SETTINGS,
        "CLUSTER_NAME": PLACEHOLDERS["clusterName"],
        "CHANGES_QUEUE_URL": PLACEHOLDERS["changesQueueUrl"],
        "CHANGES_DLQ_URL": PLACEHOLDERS["changesDlqUrl"],
        "TRAIL_NAME": PLACEHOLDERS["trailName"],
        "CANARY_SECURITY_GROUP_ID": PLACEHOLDERS["canarySecurityGroupId"],
        "CERTIFICATE_ARN": CERTIFICATE_ARN,
        "DEMO_BUCKET": PLACEHOLDERS["demoBucket"],
    }
    assert run(tmp_path, env, source="env").returncode == 0
    values = generated(tmp_path)
    expected = {k: v for k, v in PLACEHOLDERS.items() if k != "vpcId"}
    assert {k: values[k] for k in expected} == expected
    # The deploy role can't see the VPC, and tier=app doesn't use it.
    assert "vpcId" not in values

    del env["TRAIL_NAME"]
    result = run(tmp_path, env, source="env")
    assert result.returncode == 1
    assert "TRAIL_NAME" in result.stderr


def test_none_source_writes_only_the_env_keys(tmp_path: Path) -> None:
    assert run(tmp_path, ENV_SETTINGS, source="none").returncode == 0
    values = generated(tmp_path)
    assert "clusterName" not in values
    assert values["nameSuffix"] == "test"


def test_unknown_source_fails(tmp_path: Path) -> None:
    assert run(tmp_path, ENV_SETTINGS, source="cloud").returncode == 1
