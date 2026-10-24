"""deploy.yaml only ever applies tier=app, and every action in it is pinned to a commit SHA.

The file waits in docs/proposed-workflows/ until a human moves it to .github/workflows/ (the
agent token can't push there); the test reads whichever exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CANDIDATES = [
    REPO / ".github" / "workflows" / "deploy.yaml",
    REPO / "docs" / "proposed-workflows" / "deploy.yaml",
]


@pytest.fixture(scope="module")
def text() -> str:
    path = next(p for p in CANDIDATES if p.exists())
    return path.read_text()


@pytest.fixture(scope="module")
def workflow(text: str) -> dict:
    return yaml.safe_load(text)


def steps(workflow: dict) -> list[dict]:
    return [s for job in workflow["jobs"].values() for s in job["steps"]]


def test_actions_are_pinned_to_commit_shas(workflow: dict) -> None:
    uses = [s["uses"] for s in steps(workflow) if "uses" in s]
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), ref


def test_only_tier_app_is_applied(text: str, workflow: dict) -> None:
    # The only apply is `make deploy`, which is helmfile -l tier=app apply (Makefile).
    commands = "\n".join(s.get("run", "") for s in steps(workflow))
    assert re.search(r"^\s*make deploy\s*$", commands, re.MULTILINE)
    for forbidden in (
        "make addons",
        "tier=addons",
        "helmfile",
        "helm upgrade",
        "helm install",
    ):
        assert forbidden not in commands
    assert "kubectl apply" not in commands
    makefile = (REPO / "Makefile").read_text()
    assert re.search(
        r"^\s*\$\(HELMFILE_EKS\) -l tier=app .*\bapply$", makefile, re.MULTILINE
    )


def test_runs_after_ci_on_main_and_by_hand(workflow: dict) -> None:
    on = (
        workflow[True] if True in workflow else workflow["on"]
    )  # YAML 1.1 reads `on` as True
    assert on["workflow_run"]["workflows"] == ["ci"]
    assert on["workflow_run"]["branches"] == ["main"]
    assert "workflow_dispatch" in on


def test_deploy_job_uses_the_environment_session_name_and_masks_the_account(
    workflow: dict,
) -> None:
    job = workflow["jobs"]["deploy"]
    assert job["environment"] == "consoletrack-deploy"
    assert job["permissions"]["id-token"] == "write"
    aws = next(
        s for s in job["steps"] if "configure-aws-credentials" in s.get("uses", "")
    )
    assert aws["with"]["role-session-name"] == "gh-${{ github.run_id }}"
    assert any("add-mask" in s.get("run", "") for s in job["steps"])


def test_images_job_pushes_with_github_token_only(workflow: dict, text: str) -> None:
    job = workflow["jobs"]["images"]
    assert job["permissions"] == {"contents": "read", "packages": "write"}
    assert "id-token" not in job["permissions"]
    assert "secrets.GITHUB_TOKEN" in text
    # The images job runs before, and without, any AWS access.
    assert not any(
        "configure-aws-credentials" in s.get("uses", "") for s in job["steps"]
    )


def test_every_deploy_step_after_the_cluster_check_is_conditional(
    workflow: dict,
) -> None:
    deploy = workflow["jobs"]["deploy"]["steps"]
    check = next(i for i, s in enumerate(deploy) if s.get("id") == "cluster")
    for step in deploy[check + 1 :]:
        assert step["if"] == "steps.cluster.outputs.exists == 'true'", step.get("name")
