"""The remediation policy config: what the chart ships, and what it refuses to load."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from app.enums import Confidence, RemediationKind, RemediationMode
from app.main import create_app
from app.services.remediation_policy import (
    NO_POLICIES,
    PolicyConfigError,
    load_policies,
    parse_policies,
)
from app.settings import Settings
from tests.unit.fakes import FakeStore
from tests.unit.remediation_fakes import shipped_policies


def test_the_chart_ships_only_the_oom_bump_in_auto() -> None:
    config = shipped_policies()
    assert config.auto_enabled
    oom = config.policy_for(RemediationKind.set_memory_limit, "demo")
    assert oom.name == "oom-memory-bump" and oom.mode is RemediationMode.auto
    assert oom.bounds.max_multiplier == 2 and oom.bounds.ceiling == "512Mi"
    assert oom.min_confidence is Confidence.high
    assert oom.rate_limit.per_workload == 1 and oom.rate_limit.window == timedelta(hours=1)
    assert oom.verify.rollout_timeout == timedelta(minutes=3)
    assert oom.verify.wait_for_resolved == timedelta(minutes=5)
    assert oom.verify.error_ratio is not None and oom.verify.error_ratio.below == 0.05
    # Everything else falls to the manual default.
    for kind, namespace in [
        (RemediationKind.set_memory_limit, "consoletrack"),
        (RemediationKind.rollback, "demo"),
        (RemediationKind.revert_workflow, None),
        (RemediationKind.codify_pr, None),
    ]:
        policy = config.policy_for(kind, namespace)
        assert (policy.name, policy.mode) == ("default", RemediationMode.manual)
    assert [p.name for p in config.policies if p.mode is RemediationMode.auto] == [
        "oom-memory-bump"
    ]


@pytest.mark.parametrize("kind", ["revert_workflow", "codify_pr"])
def test_aws_kinds_can_never_be_auto(kind: str) -> None:
    with pytest.raises(PolicyConfigError, match="changes AWS and can never be auto"):
        parse_policies(
            f"policies: [{{name: aws, match: {{kind: {kind}, namespace: demo}}, mode: auto}}]"
        )
    # Manual is fine.
    parse_policies(f"policies: [{{name: aws, match: {{kind: {kind}}}, mode: manual}}]")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("policies: [{name: all, match: {}, mode: auto}]", "auto needs match.kind"),
        ("policies: [{name: rb, match: {kind: rollback, namespace: demo}, mode: auto}]", "auto"),
        ("policies: [{name: m, match: {kind: set_memory_limit}, mode: auto}]", "match.namespace"),
        ("policies: [{name: m, bounds: {maxMultiplier: 4}}]", "maxMultiplier may not exceed 2"),
        ("policies: [{name: m, bounds: {ceiling: 1Gi}}]", "ceiling may not exceed 512Mi"),
        (
            "policies: [{name: m, allowedChanges: [spec.template.spec.containers]}]",
            "allowedChanges",
        ),
        ("policies: [{name: m, verify: {waitForResolved: soon}}]", "not a duration"),
        ("policies: [{name: m, mode: sometimes}]", "mode"),
        ("policies: [{name: m, typo: true}]", "typo"),
        ("autoEnable: true", "autoEnable"),
        ("policies: [{name: a}, {name: a}]", "unique"),
        ("policies: [", "invalid remediation policies"),
    ],
)
def test_invalid_configs_are_refused(text: str, message: str) -> None:
    with pytest.raises(PolicyConfigError, match=message):
        parse_policies(text)


def test_no_file_means_no_policies(tmp_path: Path) -> None:
    assert load_policies(None) is NO_POLICIES
    assert not NO_POLICIES.auto_enabled
    assert NO_POLICIES.policy_for(RemediationKind.set_memory_limit, "demo").mode is (
        RemediationMode.manual
    )
    with pytest.raises(PolicyConfigError, match="can't read"):
        load_policies(str(tmp_path / "missing.yaml"))
    path = tmp_path / "policies.yaml"
    path.write_text("autoEnabled: false\npolicies: []\n")
    assert load_policies(str(path)) == NO_POLICIES


def test_the_backend_refuses_to_start_with_an_invalid_file(
    tmp_path: Path, settings: Settings, store: FakeStore
) -> None:
    path = tmp_path / "policies.yaml"
    path.write_text("policies: [{name: x, match: {kind: revert_workflow}, mode: auto}]\n")
    bad = settings.model_copy(update={"remediation_policies_file": str(path)})
    with pytest.raises(PolicyConfigError, match="can never be auto"):
        create_app(bad, uow_factory=store.uow)
