"""The only Kubernetes API calls k8s-mcp can make.

`KubeReader` is deliberately narrow: reads of pods, pod logs, events, Deployments, ReplicaSets,
and StatefulSets, matching the ServiceAccount's RBAC. There is no method for Secrets, ConfigMaps,
exec, or any write, so a tool can't reach them even by mistake; RBAC denies them again in the
cluster. Tests replace it with a fake that has the same methods.
"""

from __future__ import annotations

from typing import Protocol

from kubernetes import client, config
from kubernetes.client import (
    CoreV1EventList,
    V1Deployment,
    V1PodList,
    V1ReplicaSet,
    V1ReplicaSetList,
    V1StatefulSet,
)


class KubeReader(Protocol):
    def read_deployment(self, namespace: str, name: str) -> V1Deployment: ...

    def read_stateful_set(self, namespace: str, name: str) -> V1StatefulSet: ...

    def read_replica_set(self, namespace: str, name: str) -> V1ReplicaSet: ...

    def list_replica_sets(self, namespace: str, label_selector: str) -> V1ReplicaSetList: ...

    def list_pods(self, namespace: str, label_selector: str, limit: int) -> V1PodList: ...

    def read_pod_log(
        self,
        namespace: str,
        pod: str,
        container: str | None,
        previous: bool,
        tail_lines: int,
        limit_bytes: int,
    ) -> str: ...

    def list_events(self, namespace: str, field_selector: str, limit: int) -> CoreV1EventList: ...


class KubernetesReader:
    """`KubeReader` backed by the official client: in-cluster config, else kubeconfig."""

    def __init__(
        self,
        context: str | None = None,
        request_timeout: float = 10.0,
        api_client: client.ApiClient | None = None,
    ) -> None:
        # api_client is for the kind tests, which connect with the ServiceAccount's own token.
        if api_client is None:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config(context=context)
        self._core = client.CoreV1Api(api_client)
        self._apps = client.AppsV1Api(api_client)
        self._timeout = request_timeout

    def read_deployment(self, namespace: str, name: str) -> V1Deployment:
        return self._apps.read_namespaced_deployment(
            name, namespace, _request_timeout=self._timeout
        )

    def read_stateful_set(self, namespace: str, name: str) -> V1StatefulSet:
        return self._apps.read_namespaced_stateful_set(
            name, namespace, _request_timeout=self._timeout
        )

    def read_replica_set(self, namespace: str, name: str) -> V1ReplicaSet:
        return self._apps.read_namespaced_replica_set(
            name, namespace, _request_timeout=self._timeout
        )

    def list_replica_sets(self, namespace: str, label_selector: str) -> V1ReplicaSetList:
        return self._apps.list_namespaced_replica_set(
            namespace, label_selector=label_selector, _request_timeout=self._timeout
        )

    def list_pods(self, namespace: str, label_selector: str, limit: int) -> V1PodList:
        return self._core.list_namespaced_pod(
            namespace, label_selector=label_selector, limit=limit, _request_timeout=self._timeout
        )

    def read_pod_log(
        self,
        namespace: str,
        pod: str,
        container: str | None,
        previous: bool,
        tail_lines: int,
        limit_bytes: int,
    ) -> str:
        kwargs = {"container": container} if container else {}
        return self._core.read_namespaced_pod_log(
            pod,
            namespace,
            previous=previous,
            tail_lines=tail_lines,
            limit_bytes=limit_bytes,
            timestamps=True,
            _request_timeout=self._timeout,
            **kwargs,
        )

    def list_events(self, namespace: str, field_selector: str, limit: int) -> CoreV1EventList:
        return self._core.list_namespaced_event(
            namespace, field_selector=field_selector, limit=limit, _request_timeout=self._timeout
        )
