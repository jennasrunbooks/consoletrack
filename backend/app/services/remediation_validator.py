"""The deterministic remediation validator: no model involved, the same rules for an auto fix and
a human's approval (product-spec.md → Remediation modes; docs/remediation-graduation.md).

`set_memory_limit` is accepted only when:

- the namespace is in K8S_ALLOWED_NAMESPACES, the object is a Deployment, and the named
  container exists in its pod template;
- the patch changes only that container's `resources.limits.memory`, plus `requests.memory`
  when the current request would exceed the new limit (both must be in the policy's
  `allowedChanges`);
- the new limit is above the current one, at most `maxMultiplier` times it, and at most the
  policy's `ceiling` (never more than 2x or 512Mi, remediation_policy.py).

`check_proposal` runs the checks that need no cluster (the worker's policy gate);
`plan_memory_patch` runs all of them against the live Deployment (the backend, just before the
dry run), and `only_memory_changed` checks the server's dry-run result changed nothing else.
Anything refused raises `Refused` with the reason, which is recorded on the remediation.
"""

from __future__ import annotations

import copy
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from app.agents.schema import SetMemoryLimit
from app.services.quantity import InvalidQuantity, format_mi, parse_bytes
from app.services.remediation_policy import Policy

LIMIT_FIELD = "resources.limits.memory"
REQUEST_FIELD = "resources.requests.memory"


class Refused(Exception):
    """The proposal fails a validator rule; the message says which."""


@dataclass(frozen=True)
class MemoryPatch:
    namespace: str
    deployment: str
    container: str
    # The container's memory before and after: {"limits": {"memory": ...}, "requests": {...}}.
    before: dict[str, Any]
    after: dict[str, Any]

    def body(self) -> dict[str, Any]:
        """Strategic-merge patch that sets `after`."""
        return _container_patch(self.container, self.after)

    def undo_body(self) -> dict[str, Any]:
        """Strategic-merge patch that puts `before` back; a request that wasn't set is removed.
        The pod template then equals the previous revision's again, so the Deployment rolls back
        to that ReplicaSet, as `kubectl rollout undo` would, without touching anything else."""
        return _container_patch(self.container, self.before)

    def summary(self) -> str:
        return f"memory limit {self.before['limits']['memory']} → {self.after['limits']['memory']}"


def _container_patch(container: str, memory: dict[str, Any]) -> dict[str, Any]:
    resources = {
        section: {"memory": memory.get(section, {}).get("memory")}
        for section in ("limits", "requests")
    }
    containers = [{"name": container, "resources": resources}]
    return {"spec": {"template": {"spec": {"containers": containers}}}}


def check_proposal(
    proposal: SetMemoryLimit, *, policy: Policy, allowed_namespaces: Collection[str]
) -> None:
    """The rules that need no cluster: namespace, allowed fields, the new value and the ceiling."""
    if proposal.namespace not in allowed_namespaces:
        raise Refused(f"namespace {proposal.namespace} is not in K8S_ALLOWED_NAMESPACES")
    if LIMIT_FIELD not in policy.allowed_changes:
        raise Refused(f"policy {policy.name} doesn't allow changing {LIMIT_FIELD}")
    new = _bytes(proposal.memory_limit, "the proposed limit")
    if new > policy.bounds.ceiling_bytes:
        raise Refused(
            f"the proposed limit {proposal.memory_limit} is above the {policy.bounds.ceiling} "
            "ceiling"
        )


def plan_memory_patch(
    proposal: SetMemoryLimit,
    deployment: dict[str, Any],
    *,
    policy: Policy,
    allowed_namespaces: Collection[str],
) -> MemoryPatch:
    """Every rule, against the live Deployment (as the API returns it, camelCase)."""
    check_proposal(proposal, policy=policy, allowed_namespaces=allowed_namespaces)
    if deployment.get("kind") != "Deployment":
        kind = deployment.get("kind") or "unknown kind"
        raise Refused(f"the target is a {kind}, not a Deployment")
    metadata = deployment.get("metadata") or {}
    if (metadata.get("namespace"), metadata.get("name")) != (
        proposal.namespace,
        proposal.deployment,
    ):
        raise Refused("the Deployment returned isn't the one proposed")
    container = _container(deployment, proposal.container)
    if container is None:
        raise Refused(
            f"Deployment {proposal.namespace}/{proposal.deployment} has no container "
            f"{proposal.container}"
        )
    resources = container.get("resources") or {}
    current_limit = (resources.get("limits") or {}).get("memory")
    current_request = (resources.get("requests") or {}).get("memory")
    if current_limit is None:
        raise Refused(f"container {proposal.container} has no memory limit to raise")
    current = _bytes(current_limit, "the current limit")
    new = _bytes(proposal.memory_limit, "the proposed limit")
    if new <= current:
        raise Refused(
            f"the proposed limit {proposal.memory_limit} is not above the current {current_limit}"
        )
    if new > current * policy.bounds.max_multiplier:
        raise Refused(
            f"the proposed limit {proposal.memory_limit} is more than "
            f"{policy.bounds.max_multiplier:g}x the current {current_limit} "
            f"(at most {format_mi(int(current * policy.bounds.max_multiplier))})"
        )
    before: dict[str, Any] = {"limits": {"memory": current_limit}}
    after: dict[str, Any] = {"limits": {"memory": proposal.memory_limit}}
    if current_request is not None:
        before["requests"] = {"memory": current_request}
        after["requests"] = {"memory": current_request}
        if _bytes(current_request, "the current request") > new:
            if REQUEST_FIELD not in policy.allowed_changes:
                raise Refused(
                    f"the request {current_request} would exceed the new limit and policy "
                    f"{policy.name} doesn't allow changing {REQUEST_FIELD}"
                )
            after["requests"] = {"memory": proposal.memory_limit}
    return MemoryPatch(
        namespace=proposal.namespace,
        deployment=proposal.deployment,
        container=proposal.container,
        before=before,
        after=after,
    )


def only_memory_changed(before: dict[str, Any], after: dict[str, Any], patch: MemoryPatch) -> None:
    """The dry run's result differs from the live Deployment's pod template only in the patched
    container's memory, and that memory is what the patch says."""
    old = copy.deepcopy((before.get("spec") or {}).get("template") or {})
    new = copy.deepcopy((after.get("spec") or {}).get("template") or {})
    old_container = _template_container(old, patch.container)
    new_container = _template_container(new, patch.container)
    if old_container is None or new_container is None:
        raise Refused(f"the dry run lost container {patch.container}")
    got = _memory(new_container)
    if got != _normalize(patch.after):
        raise Refused(f"the dry run set {got}, not {patch.after}")
    for container in (old_container, new_container):
        for section in ("limits", "requests"):
            (container.get("resources") or {}).get(section, {}).pop("memory", None)
        _drop_empty(container)
    if old != new:
        raise Refused("the dry run changed more of the pod template than the container's memory")


def _bytes(value: Any, what: str) -> int:
    try:
        return parse_bytes(value)
    except InvalidQuantity:
        raise Refused(f"{what} {value!r} is not a memory quantity the validator compares") from None


def _container(deployment: dict[str, Any], name: str) -> dict[str, Any] | None:
    return _template_container((deployment.get("spec") or {}).get("template") or {}, name)


def _template_container(template: dict[str, Any], name: str) -> dict[str, Any] | None:
    containers = (template.get("spec") or {}).get("containers") or []
    return next((c for c in containers if c.get("name") == name), None)


def _memory(container: dict[str, Any]) -> dict[str, Any]:
    resources = container.get("resources") or {}
    return _normalize(
        {
            section: {"memory": (resources.get(section) or {}).get("memory")}
            for section in ("limits", "requests")
        }
    )


def _normalize(memory: dict[str, Any]) -> dict[str, Any]:
    return {
        section: {"memory": parse_bytes(value["memory"])}
        for section, value in memory.items()
        if value and value.get("memory") is not None
    }


def _drop_empty(container: dict[str, Any]) -> None:
    resources = container.get("resources")
    if not isinstance(resources, dict):
        return
    for section in ("limits", "requests"):
        if resources.get(section) == {}:
            del resources[section]
    if not resources:
        del container["resources"]
