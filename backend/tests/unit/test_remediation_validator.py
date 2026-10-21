"""The deterministic validator for set_memory_limit (no model, no cluster)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.agents.schema import SetMemoryLimit
from app.services.quantity import InvalidQuantity, parse_bytes
from app.services.remediation_validator import (
    Refused,
    check_proposal,
    only_memory_changed,
    plan_memory_patch,
)
from tests.unit.remediation_fakes import OOM_PROPOSAL, deployment, policies

ALLOWED = frozenset({"demo"})
POLICY = policies().policy_for("set_memory_limit", "demo")


def proposal(**fields: Any) -> SetMemoryLimit:
    return SetMemoryLimit.model_validate({**OOM_PROPOSAL, **fields})


def plan(obj: dict[str, Any] | None = None, **fields: Any) -> Any:
    return plan_memory_patch(
        proposal(**fields),
        obj if obj is not None else deployment(),
        policy=POLICY,
        allowed_namespaces=ALLOWED,
    )


def test_the_oom_fix_is_allowed() -> None:
    patch = plan()
    assert patch.before == {"limits": {"memory": "128Mi"}, "requests": {"memory": "128Mi"}}
    assert patch.after == {"limits": {"memory": "256Mi"}, "requests": {"memory": "128Mi"}}
    assert patch.summary() == "memory limit 128Mi → 256Mi"
    # Only the container's memory, by name, in a strategic-merge patch.
    assert patch.body() == {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "demo-app",
                            "resources": {
                                "limits": {"memory": "256Mi"},
                                "requests": {"memory": "128Mi"},
                            },
                        }
                    ]
                }
            }
        }
    }


def test_undo_restores_the_previous_values_and_removes_a_request_that_was_unset() -> None:
    patch = plan(deployment(request=None))
    containers = patch.undo_body()["spec"]["template"]["spec"]["containers"]
    assert containers == [
        {
            "name": "demo-app",
            "resources": {"limits": {"memory": "128Mi"}, "requests": {"memory": None}},
        }
    ]


@pytest.mark.parametrize(
    ("fields", "obj", "message"),
    [
        ({"namespace": "kube-system"}, None, "not in K8S_ALLOWED_NAMESPACES"),
        ({"namespace": "consoletrack"}, None, "not in K8S_ALLOWED_NAMESPACES"),
        ({"memory_limit": "257Mi"}, None, "more than 2x the current 128Mi"),
        ({"memory_limit": "1Gi"}, None, "above the 512Mi ceiling"),
        ({"memory_limit": "64Mi"}, None, "not above the current 128Mi"),
        ({"memory_limit": "128Mi"}, None, "not above the current 128Mi"),
        ({"container": "sidecar"}, None, "has no container sidecar"),
        ({}, deployment(limit=None), "has no memory limit to raise"),
        ({}, deployment(limit="0.5Gi"), "not a memory quantity"),
        ({}, {**deployment(), "kind": "StatefulSet"}, "not a Deployment"),
        ({"deployment": "other"}, None, "isn't the one proposed"),
    ],
)
def test_refused(fields: dict[str, Any], obj: dict[str, Any] | None, message: str) -> None:
    with pytest.raises(Refused, match=message):
        plan(obj, **fields)


def test_the_ceiling_wins_over_2x() -> None:
    # 2x of 300Mi is 600Mi, but never above 512Mi.
    assert plan(deployment(limit="300Mi"), memory_limit="512Mi").after["limits"] == {
        "memory": "512Mi"
    }
    with pytest.raises(Refused, match="ceiling"):
        plan(deployment(limit="300Mi"), memory_limit="600Mi")


def test_another_field_in_the_proposal_is_rejected_by_the_schema() -> None:
    # The proposal carries only the new limit: there is no field for anything else to change.
    with pytest.raises(ValidationError):
        SetMemoryLimit.model_validate({**OOM_PROPOSAL, "image": "evil:latest"})


def test_the_request_follows_only_when_it_would_exceed_the_new_limit() -> None:
    # A request above the limit can't exist in a valid Deployment; the rule still holds if the
    # API ever returns one (e.g. limits in different units than requests).
    patch = plan(deployment(limit="128Mi", request="200Mi"))
    assert patch.after == {"limits": {"memory": "256Mi"}, "requests": {"memory": "200Mi"}}
    patch = plan(deployment(limit="128Mi", request="300Mi"))
    assert patch.after["requests"] == {"memory": "256Mi"}
    limits_only = policies(allowedChanges=["resources.limits.memory"])
    with pytest.raises(Refused, match="doesn't allow changing resources.requests.memory"):
        plan_memory_patch(
            proposal(),
            deployment(limit="128Mi", request="300Mi"),
            policy=limits_only.policy_for("set_memory_limit", "demo"),
            allowed_namespaces=ALLOWED,
        )


def test_check_proposal_needs_no_cluster() -> None:
    check_proposal(proposal(), policy=POLICY, allowed_namespaces=ALLOWED)
    with pytest.raises(Refused, match="ceiling"):
        check_proposal(proposal(memory_limit="1Gi"), policy=POLICY, allowed_namespaces=ALLOWED)
    with pytest.raises(Refused, match="K8S_ALLOWED_NAMESPACES"):
        check_proposal(proposal(), policy=POLICY, allowed_namespaces=frozenset())


def dry_run_result(**container_changes: Any) -> dict[str, Any]:
    result = deployment(limit="256Mi")
    result["spec"]["template"]["spec"]["containers"][0].update(container_changes)
    return result


def test_the_dry_run_may_change_only_the_memory() -> None:
    patch = plan()
    only_memory_changed(deployment(), dry_run_result(), patch)
    with pytest.raises(Refused, match="changed more of the pod template"):
        only_memory_changed(deployment(), dry_run_result(image="evil:latest"), patch)
    with pytest.raises(Refused, match="dry run set"):
        only_memory_changed(deployment(), deployment(limit="300Mi"), patch)
    lost = deployment()
    lost["spec"]["template"]["spec"]["containers"] = []
    with pytest.raises(Refused, match="lost container"):
        only_memory_changed(deployment(), lost, patch)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("128Mi", 128 * 2**20), ("1Gi", 2**30), ("134217728", 2**27), ("500M", 500 * 10**6)],
)
def test_quantities(value: str, expected: int) -> None:
    assert parse_bytes(value) == expected


@pytest.mark.parametrize("value", ["0.5Gi", "1e9", "128mi", "-1Mi", "", None])
def test_unsupported_quantities(value: Any) -> None:
    with pytest.raises(InvalidQuantity):
        parse_bytes(value)
