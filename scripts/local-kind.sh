#!/usr/bin/env bash
# The kind half of `make local` (product-spec.md → Local run): the `consoletrack` kind cluster
# with kube-prometheus-stack and the demo app from the Helmfile `local` environment, and the
# links to the Compose stack, which runs the backend, dashboard, and k8s-mcp outside the cluster.
#
#   scripts/local-kind.sh cluster   before `docker compose up`: the cluster, the demo-app image
#                                   (built by make), the add-on and demo-app releases, the
#                                   consoletrack chart's k8s-mcp and remediator RBAC, a
#                                   kubeconfig each for Compose's k8s-mcp and backend, and the
#                                   chart's remediation policies for the backend and worker
#   scripts/local-kind.sh alerts    after it: the backend's webhook token and the
#                                   AlertmanagerConfig, then a check that Alertmanager reaches
#                                   the Compose backend
#   scripts/local-kind.sh fault oom|badImage|badProbe|off
#                                   turn one demo-app fault on, or all off (`make local-fault`)
#   scripts/local-kind.sh incidents the incidents, with the agent's root cause and proposal, from
#                                   the local API (`make local-incidents`)
#
# Only kube-prometheus-stack and demo-app are installed. Compose provides Postgres (so no
# CloudNativePG), the backend and dashboard (so no consoletrack release, Gateway API CRDs, or
# cluster-defaults); of the consoletrack chart only the pieces in compose/kind-values.yaml apply.
#
# Every kubectl, helm, and helmfile call names the kind context, so the current context (an EKS
# cluster, say) is never touched. kind adds that context to your kubeconfig as usual, which is
# also what .mcp.json uses. Re-running is safe; `make local-down` deletes the cluster.
#
# Needs docker, kind, kubectl, helm, and helmfile; the script checks them. helm-diff is optional
# but makes reruns fast: with it the releases go through `helmfile apply`, which skips unchanged
# ones; without it, `helmfile sync` upgrades kube-prometheus-stack every run (its hook Jobs, then
# waiting for every pod to be Ready again, a few minutes).
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
# Read by Compose's backend, which applies approved remediations (compose/kind.yaml); gitignored.
remediator_kubeconfig=$repo_root/kind-remediator.kubeconfig
# The chart's remediation-policies ConfigMap data, for Compose's backend and worker; gitignored.
policies_file=$repo_root/kind-remediation-policies.yaml
# Compose's backend on kind's Docker network (compose/kind.yaml).
backend_url=http://consoletrack-backend:8000
# The k8s-mcp and remediator tokens' lifetime; `make local` writes new ones on every run.
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
	local verb=(sync)
	if helm plugin list 2>/dev/null | awk 'NR > 1 { print $1 }' | grep -qx diff; then
		# A fresh cluster has no Prometheus CRDs yet, so helm-diff can't render a release that isn't
		# installed; diff only the ones that are.
		verb=(apply --suppress-diff --skip-diff-on-install)
	else
		echo "local-kind: helm-diff isn't installed, so every rerun upgrades kube-prometheus-stack;" \
			"install it for fast reruns (README → Try it without AWS)"
	fi
	echo "local-kind: helmfile -e local ${verb[0]}: kube-prometheus-stack and demo-app"
	helmfile --file "$repo_root/helm/helmfile.yaml.gotmpl" -e local --kube-context "$context" \
		-l name=kube-prometheus-stack -l name=demo-app "${verb[@]}"

	# The consoletrack chart's ServiceAccounts, k8s-mcp's read-only Roles, and the remediator's
	# patch Role in `demo`, rendered from the chart that ships to EKS. Each object names its own
	# namespace.
	echo "local-kind: applying the consoletrack chart's k8s-mcp and remediator RBAC"
	kc create namespace consoletrack --dry-run=client -o yaml | kc apply -f - >/dev/null
	render templates/rbac-k8s-mcp.yaml templates/rbac-remediator.yaml templates/serviceaccounts.yaml |
		kc apply -f -

	write_kubeconfig k8s-mcp "$mcp_kubeconfig"
	write_kubeconfig remediator "$remediator_kubeconfig"
	write_policies
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

# Compose's k8s-mcp and backend get their ServiceAccount's token, not kind's admin credentials,
# so the RBAC that ships is what limits them locally too (docs/permissions.md): k8s-mcp reads
# `demo`, the backend (remediator) patches Deployments in `demo` and nothing else. The server
# address is kind's internal one: the control-plane container's name on kind's Docker network.
#   write_kubeconfig <serviceaccount> <file>
write_kubeconfig() {
	local account=$1 file=$2 internal server ca token
	internal=$(mktemp)
	kind get kubeconfig --internal --name "$cluster" >"$internal"
	server=$(kubectl --kubeconfig "$internal" config view --raw -o jsonpath='{.clusters[0].cluster.server}')
	ca=$(kubectl --kubeconfig "$internal" config view --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')
	rm -f "$internal"
	token=$(kc -n consoletrack create token "$account" --duration "$token_duration")
	[ -n "$server" ] && [ -n "$ca" ] && [ -n "$token" ] || die "couldn't build the $account kubeconfig"

	cat >"$file.tmp" <<-EOF
		apiVersion: v1
		kind: Config
		clusters:
		  - name: $cluster
		    cluster:
		      server: $server
		      certificate-authority-data: $ca
		users:
		  - name: $account
		    user:
		      token: $token
		contexts:
		  - name: $account
		    context:
		      cluster: $cluster
		      user: $account
		      namespace: demo
		current-context: $account
	EOF
	# The containers run as uid 10001 and read it through a read-only bind mount. The token is
	# limited to the demo namespace by the chart's RBAC, and expires.
	chmod 0644 "$file.tmp"
	mv "$file.tmp" "$file"
	echo "local-kind: wrote $(basename "$file") ($account ServiceAccount token, $token_duration)"
}

# The same policies.yaml the remediation-policies ConfigMap carries on EKS
# (helm/charts/consoletrack/values.yaml → remediation), so the gate behaves the same locally.
write_policies() {
	render templates/remediation-policies.yaml |
		kc create --dry-run=client -f - -o jsonpath='{.data.policies\.yaml}' >"$policies_file.tmp"
	[ -s "$policies_file.tmp" ] || die "couldn't render the remediation policies"
	chmod 0644 "$policies_file.tmp"
	mv "$policies_file.tmp" "$policies_file"
	echo "local-kind: wrote $(basename "$policies_file") (the chart's remediation policies)"
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

# demoFault also turns off Helmfile's wait and atomic for this sync (helm/helmfile.yaml.gotmpl): a
# faulted pod never becomes Ready, and the rollback after the timeout would undo the fault. `off`
# syncs without it, which resets demo-app to its normal values.
fault() {
	local args=()
	case "${1:-}" in
	oom | badImage | badProbe) args=(--state-values-set "demoFault=$1") ;;
	off) ;;
	*) die "usage: make local-fault FAULT=oom|badImage|badProbe|off" ;;
	esac
	[ "${HELM_DRIVER:-}" = configmap ] || die "run this through \`make local-fault\` (HELM_DRIVER=configmap)"
	# ${args[@]+...}: an empty array under set -u fails in macOS's bash 3.2.
	helmfile --file "$repo_root/helm/helmfile.yaml.gotmpl" -e local --kube-context "$context" \
		-l name=demo-app sync ${args[@]+"${args[@]}"}
	if [ "$1" = off ]; then
		echo "local-kind: fault off; the alert resolves and its incident follows in about 3 minutes"
	else
		echo "local-kind: $1 on; expect an incident in about 4 minutes (make local-incidents)"
	fi
}

# Until the dashboard's Incidents page lands: log in as admin with the generated local password
# (never printed) and show each incident with the agent's analysis and proposal.
incidents() {
	local pw jar base id
	for tool in curl jq; do
		command -v "$tool" >/dev/null || die "$tool is not installed"
	done
	pw=$($compose exec -T backend cat /run/consoletrack/admin-password 2>/dev/null) ||
		die "no generated admin password (ADMIN_PASSWORD_HASH is set); log in to the dashboard instead"
	base=http://localhost:${DASHBOARD_PORT:-8080}
	jar=$(mktemp)
	jq -n --arg p "$pw" '{username: "admin", password: $p}' |
		curl -fsS -c "$jar" -o /dev/null -H 'Content-Type: application/json' --data @- \
			"$base/api/v1/auth/login" || {
		rm -f "$jar"
		die "login failed at $base"
	}
	for id in $(curl -fsS -b "$jar" "$base/api/v1/incidents?page_size=100" | jq -r '.items[].id'); do
		curl -fsS -b "$jar" "$base/api/v1/incidents/$id" | jq '{
			id, alertname, workload, status, opened_at, resolved_at,
			root_cause: .analysis.root_cause, confidence: .analysis.confidence,
			agent_run: (.analysis.agent_run | if . then {model, prompt_version, escalated, tool_calls, outcome} else null end),
			remediation: (.remediation | if . then {
				kind, status, mode, policy, gate_reason, decided_by, payload, before, after,
				steps: [.steps[]? | "\(.step) \(if .ok then "ok" else "FAILED" end): \(.detail)"]
			} else null end)
		}'
	done
	rm -f "$jar"
}

case "${1:-}" in
cluster) cluster_up ;;
alerts) alerts ;;
fault) fault "${2:-}" ;;
incidents) incidents ;;
*) die "usage: $0 cluster|alerts|fault <oom|badImage|badProbe|off>|incidents" ;;
esac
