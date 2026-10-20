#!/usr/bin/env bash
# The kind half of `make local` (product-spec.md → Local run): the `consoletrack` kind cluster
# with kube-prometheus-stack and the demo app from the Helmfile `local` environment, and the
# links to the Compose stack, which runs the backend, dashboard, and k8s-mcp outside the cluster.
#
#   scripts/local-kind.sh cluster   before `docker compose up`: the cluster, the demo-app image
#                                   (built by make), the add-on and demo-app releases, the
#                                   consoletrack chart's k8s-mcp RBAC, and a kubeconfig for
#                                   Compose's k8s-mcp
#   scripts/local-kind.sh alerts    after it: the backend's webhook token and the
#                                   AlertmanagerConfig, then a check that Alertmanager reaches
#                                   the Compose backend
#
# Only kube-prometheus-stack and demo-app are installed. Compose provides Postgres (so no
# CloudNativePG), the backend and dashboard (so no consoletrack release, Gateway API CRDs, or
# cluster-defaults); of the consoletrack chart only the pieces in compose/kind-values.yaml apply.
#
# Every kubectl, helm, and helmfile call names the kind context, so the current context (an EKS
# cluster, say) is never touched. kind adds that context to your kubeconfig as usual, which is
# also what .mcp.json uses. Re-running is safe; `make local-down` deletes the cluster.
#
# Needs docker, kind, kubectl, helm, and helmfile; the script checks them. Not helm-diff: it uses
# `helmfile sync`, which needs no diff on a throwaway local cluster (`make addons` and
# `make deploy` use `apply`, which does).
# Run it through make, which exports HELM_DRIVER=configmap and DEMO_APP_IMAGE.
set -euo pipefail

die() {
	echo "local-kind: $*" >&2
	exit 1
}

# Fixed: compose/kind.yaml and the Prometheus URL in docker-compose.yaml name its node.
cluster=consoletrack
context=kind-$cluster
repo_root=$(cd "$(dirname "$0")/.." && pwd)
# Read by Compose's k8s-mcp (docker-compose.yaml); gitignored (*.kubeconfig).
mcp_kubeconfig=$repo_root/kind-internal.kubeconfig
# Compose's backend on kind's Docker network (compose/kind.yaml).
backend_url=http://consoletrack-backend:8000
# The k8s-mcp token's lifetime; `make local` writes a new one on every run.
token_duration=${K8S_MCP_TOKEN_DURATION:-24h}
compose=${COMPOSE:-docker compose}

kc() {
	kubectl --context "$context" "$@"
}

cluster_up() {
	for tool in docker kind kubectl helm helmfile; do
		command -v "$tool" >/dev/null ||
			die "$tool is not installed (README → Try it without AWS); LOCAL_KIND=false skips kind"
	done
	[ "${HELM_DRIVER:-}" = configmap ] || die "run this through \`make local\` (HELM_DRIVER=configmap)"

	if kind get clusters 2>/dev/null | grep -qx "$cluster"; then
		echo "local-kind: reusing kind cluster $cluster"
	else
		echo "local-kind: creating kind cluster $cluster"
		kind create cluster --name "$cluster" --wait 120s
	fi

	echo "local-kind: loading ${DEMO_APP_IMAGE:?} into kind"
	kind load docker-image "${DEMO_APP_IMAGE:?}" --name "$cluster"

	# demo-app needs kube-prometheus-stack (its ServiceMonitor and PrometheusRule CRDs). Several
	# -l selectors are ORed. Waits for both to be ready (helmDefaults).
	echo "local-kind: syncing kube-prometheus-stack and demo-app (helmfile -e local)"
	helmfile --file "$repo_root/helm/helmfile.yaml.gotmpl" -e local --kube-context "$context" \
		-l name=kube-prometheus-stack -l name=demo-app sync

	# The consoletrack chart's ServiceAccounts and k8s-mcp's read-only Roles in `demo`, rendered
	# from the chart that ships to EKS. Each object names its own namespace.
	echo "local-kind: applying the consoletrack chart's k8s-mcp RBAC"
	kc create namespace consoletrack --dry-run=client -o yaml | kc apply -f - >/dev/null
	render templates/rbac-k8s-mcp.yaml templates/serviceaccounts.yaml | kc apply -f -

	write_mcp_kubeconfig
}

# Templates of the consoletrack chart with compose/kind-values.yaml.
render() {
	local args=()
	for template in "$@"; do
		args+=(--show-only "$template")
	done
	helm template consoletrack "$repo_root/helm/charts/consoletrack" -n consoletrack \
		-f "$repo_root/compose/kind-values.yaml" "${args[@]}"
}

# Compose's k8s-mcp gets the ServiceAccount's token, not kind's admin credentials, so the RBAC
# that ships is what limits it locally too (docs/permissions.md → k8s-mcp). The server address is
# kind's internal one: the control-plane container's name on kind's Docker network.
write_mcp_kubeconfig() {
	local internal server ca token
	internal=$(mktemp)
	kind get kubeconfig --internal --name "$cluster" >"$internal"
	server=$(kubectl --kubeconfig "$internal" config view --raw -o jsonpath='{.clusters[0].cluster.server}')
	ca=$(kubectl --kubeconfig "$internal" config view --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')
	rm -f "$internal"
	token=$(kc -n consoletrack create token k8s-mcp --duration "$token_duration")
	[ -n "$server" ] && [ -n "$ca" ] && [ -n "$token" ] || die "couldn't build the k8s-mcp kubeconfig"

	cat >"$mcp_kubeconfig.tmp" <<-EOF
		apiVersion: v1
		kind: Config
		clusters:
		  - name: $cluster
		    cluster:
		      server: $server
		      certificate-authority-data: $ca
		users:
		  - name: k8s-mcp
		    user:
		      token: $token
		contexts:
		  - name: k8s-mcp
		    context:
		      cluster: $cluster
		      user: k8s-mcp
		      namespace: demo
		current-context: k8s-mcp
	EOF
	# k8s-mcp runs as uid 10001 and reads it through a read-only bind mount. The token can only
	# read the demo namespace, and expires.
	chmod 0644 "$mcp_kubeconfig.tmp"
	mv "$mcp_kubeconfig.tmp" "$mcp_kubeconfig"
	echo "local-kind: wrote $(basename "$mcp_kubeconfig") (k8s-mcp ServiceAccount token, $token_duration)"
}

alerts() {
	# The same token on both sides: the backend's (from the shell or .env, else generated by
	# backend/app/compose_entrypoint.py) becomes Secret consoletrack-webhook, which the
	# AlertmanagerConfig references. Passed on stdin, never on a command line.
	local token
	if [ -n "${ALERT_WEBHOOK_TOKEN:-}" ]; then
		token=$ALERT_WEBHOOK_TOKEN
	else
		token=$($compose exec -T backend cat /run/consoletrack/alert-webhook-token) ||
			die "couldn't read the backend's generated webhook token; is the Compose stack up?"
	fi
	printf '%s' "$token" |
		kc -n consoletrack create secret generic consoletrack-webhook --from-file=token=/dev/stdin \
			--dry-run=client -o yaml |
		kc apply -f - >/dev/null
	echo "local-kind: Secret consoletrack/consoletrack-webhook matches the backend's token"

	# After the Secret it references, so the operator never sees it missing. Sends every alert
	# from `demo` to the Compose backend (compose/kind-values.yaml → monitoring.webhookUrl).
	render templates/monitoring.yaml | kc apply -f -

	# Alertmanager's own pod, so this checks the path alerts take: pod DNS, kind's network, and
	# the backend's alias on it. The alertmanager image is busybox-based (wget).
	local pod
	pod=$(kc -n monitoring get pods -l app.kubernetes.io/name=alertmanager -o name | head -n 1)
	[ -n "$pod" ] || die "no Alertmanager pod in monitoring"
	if kc -n monitoring exec "$pod" -c alertmanager -- wget -q -T 5 -O /dev/null "$backend_url/readyz"; then
		echo "local-kind: Alertmanager reaches the Compose backend at $backend_url"
	else
		die "Alertmanager can't reach $backend_url; is the backend on kind's network (compose/kind.yaml)?"
	fi
}

case "${1:-}" in
cluster) cluster_up ;;
alerts) alerts ;;
*) die "usage: $0 cluster|alerts" ;;
esac
