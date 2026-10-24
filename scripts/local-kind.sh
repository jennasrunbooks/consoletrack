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
#   scripts/local-kind.sh measure   demo-app's memory under the OOM fault with a 512Mi limit
#                                   (`make local-measure`, #79)
#   scripts/local-kind.sh oom-prepare, oom-run
#                                   one OOM acceptance run, around a database reset
#                                   (`make local-oom-run`, #79)
#   scripts/local-kind.sh eval-oom  N of those runs and a Markdown results table (`make local-eval-oom`, #60)
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

	local pod
	pod=$(kc -n monitoring get pods -l app.kubernetes.io/name=alertmanager -o name | head -n 1)
	[ -n "$pod" ] || die "no Alertmanager pod in monitoring"
	probe_backend "$pod"
}

# From Alertmanager's own pod, so this checks the path alerts take: pod DNS, kind's network, and
# the backend's alias on it. The alertmanager image is busybox-based (wget), and that wget
# sometimes crashes (exit 139, a segfault) on a path that works. So each attempt is retried, and
# a crash (exit >= 128, killed by a signal) says nothing about the network: only a real failure
# (wget or kubectl exiting 1-127) stops make local. If every attempt crashed, it warns and goes on.
probe_attempts=${LOCAL_KIND_PROBE_ATTEMPTS:-5}
probe_delay=${LOCAL_KIND_PROBE_DELAY:-2}
probe_backend() {
	local pod=$1 attempt rc failed=0 crashed=0
	for attempt in $(seq 1 "$probe_attempts"); do
		rc=0
		kc -n monitoring exec "$pod" -c alertmanager -- \
			wget -q -T 5 -O /dev/null "$backend_url/readyz" || rc=$?
		if [ "$rc" -eq 0 ]; then
			echo "local-kind: Alertmanager reaches the Compose backend at $backend_url"
			return 0
		elif [ "$rc" -ge 128 ]; then
			crashed=$rc
			echo "local-kind: attempt $attempt: wget in the Alertmanager pod crashed (exit $rc); retrying" >&2
		else
			failed=$rc
			echo "local-kind: attempt $attempt: Alertmanager can't reach $backend_url yet (exit $rc)" >&2
		fi
		[ "$attempt" -eq "$probe_attempts" ] || sleep "$probe_delay"
	done
	if [ "$failed" -ne 0 ]; then
		die "Alertmanager can't reach $backend_url (exit $failed); is the backend on kind's network (compose/kind.yaml)?"
	fi
	echo "local-kind: warning: couldn't check the alert path: wget in the Alertmanager pod crashed" \
		"on every attempt (exit $crashed). The connection itself may be fine; after a fault," \
		"\`docker compose logs backend | grep alerts/webhook\` should show 202 Accepted." >&2
}

# demoFault also turns off Helmfile's wait and atomic for this sync (helm/helmfile.yaml.gotmpl): a
# faulted pod never becomes Ready, and the rollback after the timeout would undo the fault. `off`
# syncs without it, which resets demo-app to its normal values. On EKS (`make fault`,
# MEASURE_TARGET=eks) `off` recreates demo-app instead: after an auto fix the remediator owns the
# memory limit, and Helm's server-side apply would conflict with it (see recreate_demo_app).
fault() {
	local args=() usage="make local-fault FAULT=oom|badImage|badProbe|off (EKS: make fault FAULT=oom|badImage|badProbe|off)"
	case "${1:-}" in
	oom | badImage | badProbe) args=(--state-values-set "demoFault=$1") ;;
	off) ;;
	*) die "usage: $usage" ;;
	esac
	[ "${HELM_DRIVER:-}" = configmap ] || die "run this through \`make local-fault\` or \`make fault\` (HELM_DRIVER=configmap)"
	[ "$measure_target" = local ] || read_eks_image_tag
	if [ "$measure_target" = eks ] && [ "$1" = off ]; then
		recreate_demo_app
	else
		sync_demo_app ${args[@]+"${args[@]}"}
	fi
	if [ "$1" = off ]; then
		echo "local-kind: fault off; the alert resolves and its incident follows in about 3 minutes"
	elif [ "$measure_target" = eks ]; then
		echo "local-kind: $1 on ($context, image tag $eks_image_tag); expect an incident in about 4-8 minutes;" \
			"watch it on the dashboard"
	else
		echo "local-kind: $1 on; expect an incident in about 4 minutes (make local-incidents)"
	fi
}

# The image tag demo-app runs on EKS, read once before anything changes (see sync_demo_app).
read_eks_image_tag() {
	eks_image_tag=$(live_image_tag)
	[ -n "$eks_image_tag" ] || die "demo-app isn't deployed on $context (or its image has no tag);" \
		"deploy it first: make deploy IMAGE_TAG=<the commit the cluster runs>"
}

sync_demo_app() {
	local env=local tag=()
	if [ "$measure_target" = eks ]; then
		# The image the cluster ran when the measurement started (measure reads it first): an
		# IMAGE_TAG from this checkout might not be in GHCR, and after the cleanup deletes the
		# Deployment there is nothing left to read it from. Never sync without one: Helm would
		# install demo-app with no image tag and wait 10 minutes for pods that can't start.
		[ -n "$eks_image_tag" ] || die "no demo-app image tag; refusing to sync demo-app on $context"
		env=eks
		tag=(--state-values-set "imageTag=$eks_image_tag")
	fi
	# ${@+...}: an empty array under set -u fails in macOS's bash 3.2.
	helmfile --file "$repo_root/helm/helmfile.yaml.gotmpl" -e "$env" --kube-context "$context" \
		-l name=demo-app sync ${tag[@]+"${tag[@]}"} ${@+"$@"}
}

# demo-app recreated with the fault off and the chart's values: one rollout revision, and every
# field Helm's again. A plain sync isn't enough after a remediation or a measurement: another
# field manager (the backend's consoletrack-remediator patch, or kubectl) owns the memory limit,
# and Helm 4's server-side apply refuses to take it back.
recreate_demo_app() {
	kc -n demo delete deployment demo-app --ignore-not-found --wait >/dev/null &&
		sync_demo_app >/dev/null
}

# The tag of the image demo-app runs now; empty when it can't be read (no Deployment, no tag).
live_image_tag() {
	local image
	image=$(kc -n demo get deployment demo-app \
		-o jsonpath='{.spec.template.spec.containers[?(@.name=="demo-app")].image}' 2>/dev/null) || return 0
	[ "${image##*:}" = "$image" ] || echo "${image##*:}"
}

# demo-app's memory under the OOM fault, with the limit raised so it never OOMs (#79): its
# working set (what the kernel's OOM killer and the container limit count), RSS, and the report
# cache's own gauge, once a minute and as peaks over the last 5 minutes. The worker is stopped
# meanwhile (no triage, no auto fix). On exit, even an interrupted one, demo-app's Deployment is
# recreated with the fault off and the chart's 128Mi limit, and the worker starts again. Recreated,
# not just synced: the 512Mi and 128Mi revisions would stay in the rollout history, and the next
# OOM triage would read "limit lowered from 512Mi" and propose a rollback (#79).
measure() {
	local minute
	[ "${HELM_DRIVER:-}" = configmap ] || die "run this through \`make local-measure\` or \`make measure\` (HELM_DRIVER=configmap)"
	for tool in curl jq; do
		command -v "$tool" >/dev/null || die "$tool is not installed"
	done
	if [ "$measure_target" = eks ]; then
		read_eks_image_tag
		echo "local-kind: measuring on $context, demo-app image tag $eks_image_tag"
	fi
	start_prometheus
	trap measure_cleanup EXIT
	echo "local-kind: baseline, no fault: working set $(promql_mib "max($working_set)")"
	if [ "$measure_target" = local ]; then
		$compose -f "$repo_root/docker-compose.yaml" -f "$repo_root/compose/kind.yaml" --profile kind stop worker
	fi
	# Through Helm, not kubectl: Helm 4 applies server-side, and a field another manager changed
	# (kubectl set resources) would make the sync that puts it back fail with a conflict.
	local cache=()
	if [ -n "$measure_cache_mi" ]; then
		# This run's cache size, to compare sizes on one image (make measure CACHE_MI=140).
		cache=(--state-values-set "demoReportCacheMi=$measure_cache_mi")
	fi
	sync_demo_app --state-values-set demoFault=oom --state-values-set demoMemoryLimit=512Mi \
		${cache[@]+"${cache[@]}"}
	kc -n demo rollout status deployment/demo-app --timeout=3m
	echo "local-kind: oom fault on, limit 512Mi, report cache ${measure_cache_mi:-the default} MiB;" \
		"sampling for $measure_minutes minutes"
	for minute in $(seq 1 "$measure_minutes"); do
		sleep "$measure_interval"
		echo "local-kind: minute $minute: working set $(promql_mib "max($working_set)")," \
			"RSS $(promql_mib "max($rss)"), report cache $(promql_mib "max($report_cache)")"
	done
	echo "local-kind: peaks over the last 5 minutes:"
	echo "  working set  $(promql_mib "max(max_over_time(${working_set}[5m]))")"
	echo "  RSS          $(promql_mib "max(max_over_time(${rss}[5m]))")"
	echo "  report cache $(promql_mib "max(max_over_time(${report_cache}[5m]))")"
}

# `make measure` runs measure against EKS (MEASURE_TARGET=eks, KUBE_CONTEXT): the eks Helmfile
# environment at the image demo-app runs now. The worker keeps running there: at 512Mi nothing
# OOMs, so no alert fires, and scaling it with kubectl would leave Helm a field conflict.
measure_target=${MEASURE_TARGET:-local}
eks_image_tag=""
case "$measure_target" in
local) ;;
eks) context=${KUBE_CONTEXT:?KUBE_CONTEXT is unset (make measure sets it)} ;;
*) die "MEASURE_TARGET must be local or eks, not $measure_target" ;;
esac
measure_minutes=${MEASURE_MINUTES:-7}
# The report cache for this measurement only (MiB); empty: the environment's.
measure_cache_mi=${MEASURE_CACHE_MI:-}
case "$measure_cache_mi" in
"" | *[!0-9]*) [ -z "$measure_cache_mi" ] || die "MEASURE_CACHE_MI must be a number of MiB, not $measure_cache_mi" ;;
esac
measure_interval=${MEASURE_INTERVAL:-60}
# Series, wrapped as max(series) now or max(max_over_time(series[5m])) for the peaks.
working_set='container_memory_working_set_bytes{namespace="demo",container="demo-app"}'
rss='container_memory_rss{namespace="demo",container="demo-app"}'
report_cache='demo_app_report_cache_bytes{namespace="demo"}'

measure_cleanup() {
	local rc=$? failed=""
	trap - EXIT
	# Each step even if the one before fails: the worker must never stay stopped.
	set +e
	stop_prometheus
	echo "local-kind: putting it back: demo-app recreated (fault off, 128Mi, one revision)"
	recreate_demo_app || failed="recreating demo-app"
	if [ "$measure_target" = local ]; then
		$compose -f "$repo_root/docker-compose.yaml" -f "$repo_root/compose/kind.yaml" --profile kind \
			start worker || failed="${failed:+$failed and }starting the worker"
	fi
	if [ -n "$failed" ]; then
		echo "local-kind: couldn't put everything back: $failed failed; rerun it by hand" >&2
		exit 1
	fi
	exit "$rc"
}

# Prometheus through `kubectl port-forward` on a free local port. Not the API server's service
# proxy: on EKS the control plane runs outside the cluster's network and the nodes' security group
# doesn't let it reach port 9090 ("dial tcp ...:9090: connect: connection timed out").
prometheus_url=""
prometheus_forward_pid=""
start_prometheus() {
	local log port=""
	log=$(mktemp)
	# kubectl itself, not the kc function: `kc ... &` would background a subshell, and $! would be
	# that subshell's PID, so stop_prometheus would leave the port-forward running.
	kubectl --context "$context" -n monitoring port-forward svc/kube-prometheus-stack-prometheus :9090 \
		>"$log" 2>&1 &
	prometheus_forward_pid=$!
	for _ in $(seq 1 30); do
		port=$(sed -nE 's/^Forwarding from 127\.0\.0\.1:([0-9]+) -> 9090$/\1/p' "$log" | head -n 1)
		[ -z "$port" ] || break
		kill -0 "$prometheus_forward_pid" 2>/dev/null || break
		sleep 1
	done
	if [ -z "$port" ]; then
		stop_prometheus
		die "couldn't port-forward to Prometheus on $context: $(cat "$log")"
	fi
	rm -f "$log"
	prometheus_url=http://127.0.0.1:$port
	# Checked before anything changes, so a failure stops here.
	[ "$(promql_value 'vector(1)')" = 1 ] || {
		stop_prometheus
		die "Prometheus on $context doesn't answer at $prometheus_url"
	}
}

stop_prometheus() {
	if [ -n "$prometheus_forward_pid" ]; then
		kill "$prometheus_forward_pid" 2>/dev/null || true
		# Reaped here, so bash doesn't report "Terminated: 15" for it.
		wait "$prometheus_forward_pid" 2>/dev/null || true
		prometheus_forward_pid=""
	fi
}

# An instant query: the first series' value, or "no data" (also when Prometheus doesn't answer).
promql_value() {
	curl -fsS --max-time 10 -G --data-urlencode "query=$1" "$prometheus_url/api/v1/query" 2>/dev/null |
		jq -r '.data.result[0].value[1] // null | if . == null then "no data" else tonumber end' 2>/dev/null ||
		echo "no data"
}

promql_mib() {
	local value
	value=$(promql_value "$1")
	if [ "$value" = "no data" ]; then echo "$value"; else jq -rn --argjson v "$value" '"\($v / 1048576 | floor) MiB"'; fi
}

# The local API as admin, with the generated local password (never printed): api_login sets
# api_base and api_jar (a cookie file the caller removes), api GETs a path.
api_login() {
	local pw
	for tool in curl jq; do
		command -v "$tool" >/dev/null || die "$tool is not installed"
	done
	pw=$($compose exec -T backend cat /run/consoletrack/admin-password 2>/dev/null) ||
		die "no generated admin password (ADMIN_PASSWORD_HASH is set); log in to the dashboard instead"
	api_base=http://localhost:${DASHBOARD_PORT:-8080}
	api_jar=$(mktemp)
	jq -n --arg p "$pw" '{username: "admin", password: $p}' |
		curl -fsS -c "$api_jar" -o /dev/null -H 'Content-Type: application/json' --data @- \
			"$api_base/api/v1/auth/login" || {
		rm -f "$api_jar"
		die "login failed at $api_base"
	}
}

api() {
	curl -fsS -b "$api_jar" "$api_base$1"
}

# Until the dashboard's Incidents page lands: each incident with the agent's analysis and proposal.
incidents() {
	local id
	api_login
	for id in $(api "/api/v1/incidents?page_size=100" | jq -r '.items[].id'); do
		api "/api/v1/incidents/$id" | jq '{
			id, alertname, workload, status, opened_at, resolved_at,
			root_cause: .analysis.root_cause, confidence: .analysis.confidence,
			agent_run: (.analysis.agent_run | if . then {model, prompt_version, escalated, tool_calls, outcome} else null end),
			remediation: (.remediation | if . then {
				kind, status, mode, policy, gate_reason, decided_by, payload, before, after,
				steps: [.steps[]? | "\(.step) \(if .ok then "ok" else "FAILED" end): \(.detail)"]
			} else null end)
		}'
	done
	rm -f "$api_jar"
}

# --- One OOM acceptance run (make local-oom-run, #79) ------------------------------------------
# oom-prepare (before the database reset): demo-app recreated with the fault off (one rollout
# revision, so the agent sees no earlier change; and Helm owns every field again); then a wait
# until the app has served two minutes without errors and Alertmanager holds no
# DemoAppHighErrorRate, so the run's alert is a new episode that reaches the fresh database.
# oom-run (after it): the fault on, the incident followed until its fix finishes or needs a human,
# a summary with PASS or FAIL, and demo-app recreated with the fault off (also when interrupted).

oom_poll=${OOM_RUN_POLL:-20}
quiet_poll=${OOM_QUIET_POLL:-15}
quiet_timeout=${OOM_QUIET_TIMEOUT:-900}
run_timeout=${OOM_RUN_TIMEOUT:-1500}
diagnose_after=${OOM_DIAGNOSE_AFTER:-480}
resolve_timeout=${OOM_RESOLVE_TIMEOUT:-300}

oom_prepare() {
	[ "${HELM_DRIVER:-}" = configmap ] || die "run this through \`make local-oom-run\` (HELM_DRIVER=configmap)"
	for tool in curl jq; do
		command -v "$tool" >/dev/null || die "$tool is not installed"
	done
	echo "local-kind: fault off; demo-app recreated (one revision, every field Helm's)"
	recreate_demo_app
	kc -n demo rollout status deployment/demo-app --timeout=3m >/dev/null
	echo "local-kind: rollout history: $(kc -n demo rollout history deployment/demo-app | grep -cE '^[0-9]+') revision(s)"
	echo "local-kind: waiting for 2 minutes without errors and no DemoAppHighErrorRate in Alertmanager"
	start_prometheus
	trap stop_prometheus EXIT
	wait_for quiet "$quiet_timeout" "$quiet_poll"
	stop_prometheus
	trap - EXIT
	echo "local-kind: quiet; ready for the run"
}

quiet() {
	local errors
	errors=$(promql_value 'sum(rate(demo_client_requests_total{namespace="demo",result!="ok"}[2m])) or vector(0)')
	[ "$errors" = 0 ] && ! alert_active
}

alert_active() {
	local pod
	pod=$(kc -n monitoring get pods -l app.kubernetes.io/name=alertmanager -o name | head -n 1)
	kc -n monitoring exec "$pod" -c alertmanager -- \
		amtool alert query --alertmanager.url=http://localhost:9093 2>/dev/null | grep -qw DemoAppHighErrorRate
}

# wait_for <check> <timeout s> <poll s>: until the check passes; a line each minute meanwhile.
wait_for() {
	local check=$1 timeout=$2 poll=$3 waited=0
	until "$check"; do
		[ "$waited" -lt "$timeout" ] || die "still not $check after $((timeout / 60)) minutes"
		sleep "$poll"
		waited=$((waited + poll))
		[ $((waited % 60)) -ge "$poll" ] || echo "local-kind:   still waiting ($((waited / 60)) min)"
	done
}

# One line per change: incident status | proposal, its status and gate mode | the last step.
oom_state='"\(.status) | \(.remediation.kind // "no proposal") \(.remediation.status // "") \(.remediation.mode // "")" +
	(if (.remediation.steps // []) | length > 0 then " | \(.remediation.steps[-1].step)" else "" end)'
# The run is over: the fix finished, or there is nothing left the run can wait for.
# shellcheck disable=SC2016 # $s is jq's, not the shell's
oom_done='(.remediation.status // "") as $s
	| ($s == "succeeded" or $s == "failed" or $s == "rejected")
	or .status == "closed"
	or (.status == "resolved" and (.remediation == null or $s == "proposed"))
	or ($s == "proposed" and .remediation.kind != "set_memory_limit")
	or (.status == "awaiting_review" and .analysis != null and .remediation == null)'

# The pass rule, shared by oom-run and eval-oom: the proposal is set_memory_limit -> 256Mi for
# demo/demo-app/demo-app and the fix succeeded.
oom_pass='.remediation.kind == "set_memory_limit" and .remediation.status == "succeeded"
	and .remediation.payload.memory_limit == "256Mi" and .remediation.payload.namespace == "demo"
	and .remediation.payload.deployment == "demo-app" and .remediation.payload.container == "demo-app"'

oom_run() {
	local started elapsed id detail state last="" hinted="" diagnosed="" total
	[ "${HELM_DRIVER:-}" = configmap ] || die "run this through \`make local-oom-run\` (HELM_DRIVER=configmap)"
	api_login
	total=$(api "/api/v1/incidents?page_size=1" | jq '.total')
	[ "$total" = 0 ] || { rm -f "$api_jar"; die "the database already has incidents; \`make local-oom-run\` resets it first"; }
	trap oom_cleanup EXIT
	echo "local-kind: OOM fault on at $(date -u +%H:%M:%SZ); an incident usually opens 4-5 minutes later"
	sync_demo_app --state-values-set demoFault=oom >/dev/null
	started=$(date +%s)
	while :; do
		sleep "$oom_poll"
		elapsed=$(($(date +%s) - started))
		if [ "$elapsed" -ge "$run_timeout" ]; then
			echo "local-kind: gave up after $((run_timeout / 60)) minutes"
			break
		fi
		id=$(api "/api/v1/incidents?page_size=100" | jq -r '[.items[].id] | min // empty')
		if [ -z "$id" ]; then
			if [ "$elapsed" -ge "$diagnose_after" ] && [ -z "$diagnosed" ]; then
				oom_diagnose
				diagnosed=1
			fi
			continue
		fi
		detail=$(api "/api/v1/incidents/$id")
		state=$(jq -r "$oom_state" <<<"$detail")
		if [ "$state" != "$last" ]; then
			printf 'local-kind: %2d:%02d incident %s: %s\n' $((elapsed / 60)) $((elapsed % 60)) "$id" "$state"
			last=$state
		fi
		jq -e "$oom_done" <<<"$detail" >/dev/null && break
		if jq -e '.remediation.status == "proposed" and .remediation.mode == "manual"' <<<"$detail" >/dev/null; then
			if [ -n "${OOM_DETAIL_FILE:-}" ]; then
				# eval-oom: nobody approves, so a manual gate is the result (auto mode didn't apply it).
				echo "local-kind: the gate sent it to a human: $(jq -r '.remediation.gate_reason' <<<"$detail"); not waiting"
				break
			elif [ -z "$hinted" ]; then
				echo "local-kind: the gate sent it to a human: $(jq -r '.remediation.gate_reason' <<<"$detail")"
				echo "local-kind: approve it at $api_base/incidents/$id (admin password: make local printed it); waiting"
				hinted=1
			fi
		fi
	done
	if [ -n "${OOM_DETAIL_FILE:-}" ]; then
		oom_await_resolved
		printf '%s\n' "${detail:-}" >"$OOM_DETAIL_FILE"
		# The tool calls behind the diagnosis, for the results file's failing runs (the database is
		# reset before the next run, so this is the only copy).
		[ -z "${id:-}" ] || api "/api/v1/incidents/$id/evidence" >"${OOM_DETAIL_FILE%.json}.evidence.json" 2>/dev/null || true
	fi
	oom_summary
}

# eval-oom's rows report the time to resolved, which comes after the fix: the alert clears once
# the new limit holds. Gives it a few minutes, with the fault still on, then takes the incident as is.
oom_await_resolved() {
	local deadline=$(($(date +%s) + resolve_timeout))
	[ -n "${detail:-}" ] && [ -n "${id:-}" ] || return 0
	jq -e "$oom_pass" <<<"$detail" >/dev/null || return 0
	until jq -e '.resolved_at != null' <<<"$detail" >/dev/null || [ "$(date +%s)" -ge "$deadline" ]; do
		sleep "$oom_poll"
		detail=$(api "/api/v1/incidents/$id")
	done
}

oom_diagnose() {
	echo "local-kind: no incident after $((diagnose_after / 60)) minutes. Alertmanager holds:"
	local pod
	pod=$(kc -n monitoring get pods -l app.kubernetes.io/name=alertmanager -o name | head -n 1)
	kc -n monitoring exec "$pod" -c alertmanager -- \
		amtool alert query --alertmanager.url=http://localhost:9093 2>/dev/null | sed 's/^/  /'
	echo "local-kind: the backend's last webhook requests (202: delivered; 401: token still reloading):"
	$compose logs backend 2>/dev/null | grep 'alerts/webhook' | tail -n 5 | sed 's/^/  /'
}

# PASS: exactly one incident, and oom_pass (above) holds for it.
oom_summary() {
	local list
	list=$(api "/api/v1/incidents?page_size=100")
	echo "local-kind: ---- result ----"
	jq -r '"  incidents: \(.total) (\([.items[].alertname] | join(", ")))"' <<<"$list"
	[ -n "${detail:-}" ] || { echo "  RESULT: FAIL (no incident)"; return 1; }
	jq -r '
		"  triage: \(.analysis.confidence // "-") confidence, prompt \(.analysis.agent_run.prompt_version // "-"), \(.analysis.agent_run.tool_calls // "-") tool calls",
		"  proposal: \(.remediation.kind // "none") \(.remediation.payload.memory_limit // "")",
		"  gate: \(.remediation.mode // "-") (\(.remediation.gate_reason // "-"))\(if .remediation.decided_by == "admin" then ", approved by admin" else "" end)",
		"  remediation: \(.remediation.status // "-")",
		"  last step: \((.remediation.steps // [])[-1] | if . then "\(.step) \(if .ok then "ok" else "FAILED" end): \(.detail)" else "-" end)"
	' <<<"$detail"
	if [ "$(jq '.total' <<<"$list")" = 1 ] && jq -e "$oom_pass" <<<"$detail" >/dev/null; then
		echo "  RESULT: PASS"
	else
		echo "  RESULT: FAIL"
		return 1
	fi
}

oom_cleanup() {
	local rc=$?
	trap - EXIT
	set +e
	rm -f "$api_jar"
	echo "local-kind: fault off; demo-app recreated"
	recreate_demo_app || {
		echo "local-kind: recreating demo-app failed; the next make local-oom-run retries it" >&2
		exit 1
	}
	exit "$rc"
}

# --- N OOM acceptance runs and a results table (make local-eval-oom, #60) ----------------------------
# Each run is oom-prepare, the database reset (EVAL_OOM_RESET, a shell command from the Makefile:
# Compose down --volumes, then `make local SKIP_BUILD=1`), and oom-run, which leaves its final
# incident in OOM_DETAIL_FILE. A run that fails, or dies early, is a FAIL row and the next one starts.
# Whatever happens, demo-app is recreated with the fault off at the end (also on Ctrl-C), and the
# table is written for the runs so far.

eval_results_dir=${EVAL_OOM_DIR:-$repo_root/eval/results}

# One run's row, as JSON, from its final incident (a missing or empty file: all dashes).
# $1 run number, $2 PASS|FAIL, $3 the detail file, $4 the row file.
eval_row() {
	local detail_json='{}'
	[ ! -s "$3" ] || detail_json=$(cat "$3")
	jq -n --argjson n "$1" --arg result "$2" --argjson d "$detail_json" '
		def ts: sub("\\.[0-9]+"; "") | sub("\\+00:00$"; "Z") | fromdateiso8601;
		def dash: if . == null or . == "" then "-" else . end;
		def mmss: "\(. / 60 | floor):\(. % 60 | floor | tostring | if length < 2 then "0" + . else . end)";
		($d.remediation // {}) as $r | ($d.analysis // {}) as $a | ($a.agent_run // {}) as $run
		| ($r.payload // {}) as $p
		| {
			run: $n, result: $result,
			diagnosis: ($a.root_cause | dash | gsub("\\s+"; " ") | gsub("[0-9]{12}"; "123456789012")
				| if length > 160 then .[0:159] + "…" else . end),
			confidence: ($a.confidence | dash),
			proposal: (if $r.kind then
				"\($r.kind) → \($p.memory_limit | dash) (\($p.namespace | dash)/\($p.deployment | dash)/\($p.container | dash))"
				else "-" end),
			mode: ($r.mode | dash), outcome: ($r.status | dash),
			model: ($run.model | dash),
			escalated: (if $run.escalated == null then "-" elif $run.escalated then "y" else "n" end),
			tool_calls: ($run.tool_calls | if . == null then "-" else tostring end),
			input_tokens: ($run.input_tokens | if . == null then "-" else tostring end),
			output_tokens: ($run.output_tokens | if . == null then "-" else tostring end),
			time_to_resolved: (if $d.opened_at and $d.resolved_at
				then (($d.resolved_at | ts) - ($d.opened_at | ts)) | mmss else "-" end)
		}' >"$4"
}

eval_table() {
	jq -rs '
		def cell: tostring | gsub("\\|"; "\\|");
		["Run", "Result", "Diagnosis", "Confidence", "Proposal", "Mode", "Remediation", "Model",
			"Escalated", "Tool calls", "Input tokens", "Output tokens", "Incident open → resolved"] as $h
		| ($h | "| " + join(" | ") + " |"), ($h | "|" + (map("---") | join("|")) + "|"),
		(.[] | [.run, .result, .diagnosis, .confidence, .proposal, .mode, .outcome, .model, .escalated,
			.tool_calls, .input_tokens, .output_tokens, .time_to_resolved]
			| "| " + (map(cell) | join(" | ")) + " |")' "$eval_dir"/row-*.json
}

# For each failing run: the full root cause and the evidence the agent saw, as the API redacted it.
# Nothing is kept for a passing run. 12-digit numbers are replaced like in the table.
eval_failures() {
	local row n
	for row in "$eval_dir"/row-*.json; do
		[ "$(jq -r .result "$row")" = FAIL ] || continue
		n=$(jq -r .run "$row")
		jq -r --slurpfile d "$eval_dir/detail-$n.json" '
			def scrub: gsub("[0-9]{12}"; "123456789012");
			"### Run \(.run)", "",
			"**Root cause:** \(($d[0].analysis.root_cause // "none") | gsub("\\s+"; " ") | scrub)", ""' "$row" 2>/dev/null ||
			printf '### Run %s\n\n' "$n"
		if [ -s "$eval_dir/detail-$n.evidence.json" ]; then
			jq -r '
				def scrub: gsub("[0-9]{12}"; "123456789012");
				(.items // [])[] | "\(.seq). `\(.tool_name)` \(.args | tojson | scrub)", "",
					"~~~~", ((.result_excerpt | scrub) | .[0:1500]), "~~~~", ""' \
				"$eval_dir/detail-$n.evidence.json" 2>/dev/null || echo "(evidence unreadable)"
		else
			echo "(no evidence saved)"
		fi
		echo
	done
}

# The summary line, the table, and the file; for the runs done so far.
eval_report() {
	local done_runs file stamp commit
	done_runs=$(find "$eval_dir" -name 'row-*.json' | wc -l | tr -d ' ')
	[ "$done_runs" -gt 0 ] || return 0
	stamp=$(date -u +%Y%m%dT%H%M%SZ)
	commit=$(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null || echo unknown)
	mkdir -p "$eval_results_dir"
	file=$eval_results_dir/eval-oom-$stamp.md
	eval_summary="eval-oom: $eval_pass/$eval_runs PASS"
	[ "$done_runs" = "$eval_runs" ] || eval_summary+=" (stopped after $done_runs run(s))"
	{
		echo "# eval-oom $stamp"
		echo
		echo "$eval_summary"
		echo
		echo "Commit $commit, kind, ${eval_runs} run(s) planned. A run passes when the proposal is"
		echo "\`set_memory_limit\` → \`256Mi\` for \`demo/demo-app/demo-app\` and the fix succeeded."
		echo
		eval_table
		if [ "$eval_pass" != "$done_runs" ]; then
			echo
			echo "## Failing runs"
			echo
			eval_failures
		fi
	} >"$file"
	eval_table
	echo "$eval_summary"
	echo "local-kind: results in ${file#"$repo_root"/}"
}

# A step's wall time, to see where a run's minutes go (the run phase is the total minus these). A failing step
# ends the run before the line prints, which is fine: the run is a FAIL either way.
eval_phase() {
	local name=$1 started
	shift
	started=$(date +%s)
	"$@"
	echo "local-kind: phase $name took $((($(date +%s) - started) / 60))m$((($(date +%s) - started) % 60))s"
}

eval_oom() {
	local n rc detail_file run_started
	eval_runs=${EVAL_OOM_N:-5}
	eval_pass=0
	case "$eval_runs" in
	"" | *[!0-9]* | 0) die "N must be a positive number of runs, not $eval_runs" ;;
	esac
	[ "${HELM_DRIVER:-}" = configmap ] || die "run this through \`make local-eval-oom\` (HELM_DRIVER=configmap)"
	[ -n "${EVAL_OOM_RESET:-}" ] || die "run this through \`make local-eval-oom\` (EVAL_OOM_RESET is the database reset)"
	for tool in curl jq; do
		command -v "$tool" >/dev/null || die "$tool is not installed"
	done
	eval_dir=$(mktemp -d)
	trap eval_cleanup EXIT
	trap 'exit 130' INT TERM
	for n in $(seq 1 "$eval_runs"); do
		echo "local-kind: ==== eval-oom run $n of $eval_runs ===="
		detail_file=$eval_dir/detail-$n.json
		run_started=$(date +%s)
		# A subshell with -e on its own, outside any && or || list (which would turn -e off inside
		# it): any step that dies fails the run, not the loop.
		set +e
		(
			set -e
			eval_phase prepare oom_prepare
			eval_phase reset bash -c "$EVAL_OOM_RESET"
			OOM_DETAIL_FILE=$detail_file oom_run
		)
		rc=$?
		set -e
		if [ "$rc" = 0 ]; then
			eval_pass=$((eval_pass + 1))
			eval_row "$n" PASS "$detail_file" "$eval_dir/row-$(printf %03d "$n").json"
		else
			eval_row "$n" FAIL "$detail_file" "$eval_dir/row-$(printf %03d "$n").json"
		fi
		echo "local-kind: eval-oom run $n: $([ "$rc" = 0 ] && echo PASS || echo FAIL) (took $((($(date +%s) - run_started) / 60)) min)"
		# 5 of 5 is the bar, so one FAIL has already decided it; the rest only add statistics.
		if [ "$rc" != 0 ] && [ -n "${EVAL_OOM_FAIL_FAST:-}" ]; then
			echo "local-kind: stopping at the first FAIL (FAIL_FAST)"
			break
		fi
	done
	[ "$eval_pass" = "$eval_runs" ] # the exit code, through eval_cleanup
}

eval_cleanup() {
	local rc=$?
	trap - EXIT
	set +e
	echo "local-kind: fault off; demo-app recreated"
	recreate_demo_app || {
		echo "local-kind: recreating demo-app failed; the next run's oom-prepare retries it" >&2
		rc=1
	}
	eval_report
	rm -rf "$eval_dir"
	exit "$rc"
}

case "${1:-}" in
cluster) cluster_up ;;
alerts) alerts ;;
fault) fault "${2:-}" ;;
incidents) incidents ;;
measure) measure ;;
oom-prepare) oom_prepare ;;
oom-run) oom_run ;;
eval-oom) eval_oom ;;
*) die "usage: $0 cluster|alerts|fault <oom|badImage|badProbe|off>|incidents|measure|oom-prepare|oom-run|eval-oom" ;;
esac
