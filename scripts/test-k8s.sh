#!/usr/bin/env bash
# k8s-mcp cluster tests (`make test-k8s`): a throwaway kind cluster, the real RBAC from
# mcp-server/k8s/rbac/, and mcp-server/k8s/tests/kind/ run against it with the ServiceAccount's
# own token. Runs in CI (docs/proposed-workflows/ci.yaml, job k8s-mcp-kind) and on a host with
# Docker; the devcontainer has no Docker, so it can't run there.
#
# Uses its own kubeconfig file, so it never reads or changes ~/.kube/config or the current
# context, and only ever talks to the cluster it created. Deletes the cluster on exit unless
# KEEP_CLUSTER=true (then re-runs reuse it).
#
# Needs kind, kubectl, docker, and uv on PATH. KIND_CLUSTER overrides the cluster name.
set -euo pipefail

die() {
	echo "test-k8s: $*" >&2
	exit 1
}

for tool in kind kubectl docker uv; do
	command -v "$tool" >/dev/null || die "$tool is not installed (CI installs it; see docs/proposed-workflows/ci.yaml)"
done

cluster=${KIND_CLUSTER:-ct-k8s-mcp-test}
repo_root=$(cd "$(dirname "$0")/.." && pwd)
workdir=$(mktemp -d)
kubeconfig="$workdir/kubeconfig"

cleanup() {
	if [ "${KEEP_CLUSTER:-false}" != true ]; then
		kind delete cluster --name "$cluster" >/dev/null 2>&1 || true
	fi
	rm -rf "$workdir"
}
trap cleanup EXIT

if kind get clusters 2>/dev/null | grep -qx "$cluster"; then
	echo "test-k8s: reusing kind cluster $cluster"
	kind export kubeconfig --name "$cluster" --kubeconfig "$kubeconfig" >/dev/null
else
	echo "test-k8s: creating kind cluster $cluster"
	kind create cluster --name "$cluster" --kubeconfig "$kubeconfig" --wait 120s
fi

cd "$repo_root/mcp-server/k8s"
K8S_MCP_KIND_KUBECONFIG="$kubeconfig" uv run --locked pytest tests/kind -m kind -q "$@"
