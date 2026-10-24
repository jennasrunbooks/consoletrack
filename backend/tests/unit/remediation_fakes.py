"""Fakes for the policy gate and the apply loop: a Deployment, a cluster that applies
strategic-merge memory patches to it, a ratio source, and a clock that moves when the loop
waits."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from app.cluster import ClusterError
from app.services.remediation_policy import PolicyConfig, parse_policies
from tests.unit.conftest import NOW

VALUES = Path(__file__).resolve().parents[3] / "helm/charts/consoletrack/values.yaml"
SELECTOR = {"app.kubernetes.io/name": "demo-app"}
OOM_PROPOSAL: dict[str, Any] = {
    "kind": "set_memory_limit",
    "namespace": "demo",
    "deployment": "demo-app",
    "container": "demo-app",
    "memory_limit": "256Mi",
    "reason": "OOMKilled at 128Mi; 2x is the narrow fix.",
}


def shipped_policies() -> PolicyConfig:
    """The `remediation:` block exactly as the chart ships it."""
    return parse_policies(yaml.safe_dump(yaml.safe_load(VALUES.read_text())["remediation"]))


def policies(**overrides: Any) -> PolicyConfig:
    """The shipped policies with top-level keys or oom-memory-bump keys replaced."""
    block = yaml.safe_load(VALUES.read_text())["remediation"]
    for key, value in overrides.items():
        if key == "autoEnabled":
            block[key] = value
        else:
            block["policies"][0][key] = value
    return parse_policies(yaml.safe_dump(block))


def deployment(
    *, limit: str | None = "128Mi", request: str | None = "128Mi", container: str = "demo-app"
) -> dict[str, Any]:
    resources: dict[str, Any] = {"limits": {"cpu": "200m"}, "requests": {"cpu": "10m"}}
    if limit is not None:
        resources["limits"]["memory"] = limit
    if request is not None:
        resources["requests"]["memory"] = request
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "demo-app", "namespace": "demo", "generation": 1},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": dict(SELECTOR)},
            "template": {
                "metadata": {"labels": dict(SELECTOR)},
                "spec": {
                    "containers": [
                        {"name": container, "image": "demo-app:1", "resources": resources}
                    ]
                },
            },
        },
        "status": {
            "observedGeneration": 1,
            "replicas": 1,
            "updatedReplicas": 1,
            "availableReplicas": 1,
        },
    }


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def wait(self, seconds: float) -> bool:
        self.now += timedelta(seconds=seconds)
        return False


class FakeCluster:
    """One Deployment. A real patch bumps the generation; the rollout finishes after
    `rollout_polls` reads (never, with None). Pods run the Deployment's current template;
    `oom_after_patch` makes the patched pods OOMKilled again. Annotations in a patch's metadata
    are applied (None removes one); a metadata-only patch makes no new revision."""

    def __init__(
        self,
        obj: dict[str, Any] | None = None,
        *,
        clock: Clock,
        rollout_polls: int | None = 1,
        oom_after_patch: bool = False,
    ) -> None:
        self.obj = obj if obj is not None else deployment()
        self.clock = clock
        self.rollout_polls = rollout_polls
        self.oom_after_patch = oom_after_patch
        self.patches: list[tuple[dict[str, Any], bool]] = []
        # The annotations on the Deployment when each real spec patch made a new revision current,
        # as the Deployment controller copies them to its ReplicaSet.
        self.revision_annotations: list[dict[str, str]] = []
        self.fail_on: set[str] = set()
        # Extra changes the "server" makes on a dry run, e.g. a mutating webhook.
        self.dry_run_extra: dict[str, Any] | None = None
        self._reads_since_patch = 0
        self._patched = False

    def get_deployment(self, namespace: str, name: str) -> dict[str, Any]:
        if "get" in self.fail_on:
            raise ClusterError("Kubernetes API 403 Forbidden")
        assert (namespace, name) == ("demo", "demo-app")
        if self._patched:
            self._reads_since_patch += 1
            if self.rollout_polls is not None and self._reads_since_patch >= self.rollout_polls:
                self.obj["status"]["observedGeneration"] = self.obj["metadata"]["generation"]
        return copy.deepcopy(self.obj)

    def patch_deployment(
        self, namespace: str, name: str, body: dict[str, Any], *, dry_run: bool
    ) -> dict[str, Any]:
        if ("dry_run" if dry_run else "patch") in self.fail_on:
            raise ClusterError("Kubernetes API 422 Unprocessable Entity")
        self.patches.append((copy.deepcopy(body), dry_run))
        result = copy.deepcopy(self.obj)
        annotations = result["metadata"].setdefault("annotations", {})
        for key, value in ((body.get("metadata") or {}).get("annotations") or {}).items():
            if value is None:
                annotations.pop(key, None)
            else:
                annotations[key] = value
        if "spec" not in body:  # metadata only: no new generation, no rollout
            if "clear" in self.fail_on:
                raise ClusterError("Kubernetes API 409 Conflict")
            if not dry_run:
                self.obj = result
            return copy.deepcopy(result)
        for change in body["spec"]["template"]["spec"]["containers"]:
            target = next(
                c
                for c in result["spec"]["template"]["spec"]["containers"]
                if c["name"] == change["name"]
            )
            for section, values in change["resources"].items():
                for key, value in values.items():
                    if value is None:
                        target["resources"].get(section, {}).pop(key, None)
                    else:
                        target["resources"].setdefault(section, {})[key] = value
        if dry_run:
            if self.dry_run_extra:
                result["spec"]["template"]["spec"]["containers"][0].update(self.dry_run_extra)
            return result
        result["metadata"]["generation"] += 1
        self.revision_annotations.append(dict(annotations))
        self.obj = result
        self._patched = True
        self._reads_since_patch = 0
        return copy.deepcopy(result)

    def list_pods(self, namespace: str, selector: dict[str, str]) -> list[dict[str, Any]]:
        assert selector == SELECTOR
        container = self.obj["spec"]["template"]["spec"]["containers"][0]
        status: dict[str, Any] = {"name": container["name"], "state": {"running": {}}}
        if self.oom_after_patch and self._patched:
            status["lastState"] = {
                "terminated": {"reason": "OOMKilled", "finishedAt": self.clock.now.isoformat()}
            }
        return [
            {
                "metadata": {"name": "demo-app-abc12"},
                "spec": {"containers": [copy.deepcopy(container)]},
                "status": {"containerStatuses": [status]},
            }
        ]

    @property
    def limit(self) -> str | None:
        container = self.obj["spec"]["template"]["spec"]["containers"][0]
        return container["resources"].get("limits", {}).get("memory")


class FakeRatio:
    def __init__(self, *values: float | None) -> None:
        self.values = list(values)
        self.queries: list[str] = []

    def ratio(self, promql: str) -> float | None:
        self.queries.append(promql)
        return self.values.pop(0) if len(self.values) > 1 else (self.values or [None])[0]
