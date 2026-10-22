#!/usr/bin/env bash
# Sets what .github/workflows/deploy.yaml reads: repository variables (non-secret settings from
# .env) and the consoletrack-deploy environment's secrets (Terraform outputs of
# consoletrack/us-east-1 that contain the account ID or would point at it).
#
# Run on your own machine, as yourself, through make so .env and AWS_PROFILE are loaded:
#   make gh-deploy-config
# after `make up` (the outputs exist only while the platform does), and again after every
# `make down` → `make up`, because the queue URLs and the canary security group change.
#
# AWS_ROLE_ARN on each environment is scripts/gh-env-secrets.sh's job (global root outputs).
# Secret values go to `gh secret set` on stdin and are never printed. Empty settings are skipped,
# so the workflow falls back to the chart's defaults for them. Safe to rerun.
set -euo pipefail

repo="${GITHUB_REPO:?set GITHUB_REPO (owner/repo) in .env or your shell}"
env_name="consoletrack-deploy"
root="terraform/consoletrack/us-east-1"

for tool in gh jq terraform; do
	command -v "$tool" >/dev/null || {
		echo "gh-deploy-config: needs $tool" >&2
		exit 1
	}
done

# Refuse to run as the coding agent's machine user (e.g. a leaked GH_TOKEN).
login=$(gh api user --jq .login)
if [[ "$login" == *-agent ]]; then
	echo "gh-deploy-config: gh is authenticated as ${login}; unset GH_TOKEN and run this as yourself." >&2
	exit 1
fi

if ! gh api "repos/${repo}/environments" --jq '.environments[].name' | grep -qx -- "$env_name"; then
	echo "gh-deploy-config: environment ${env_name} doesn't exist in ${repo}; create it first." >&2
	exit 1
fi

if ! outputs=$(terraform -chdir="$root" output -json 2>/dev/null) || [[ $(jq 'length' <<<"$outputs") -eq 0 ]]; then
	echo "gh-deploy-config: no outputs in ${root}; run make up (or make tf-apply ROOT=consoletrack/us-east-1) first." >&2
	exit 1
fi

# A Terraform output's value, or empty when it's missing or null (certificate_arn without DOMAIN).
out() {
	jq -r --arg k "$1" '.[$k].value // empty' <<<"$outputs"
}

# Plain functions, not associative arrays: macOS ships bash 3.2.
set_var() {
	if [[ -z "$2" ]]; then
		echo "skip  variable $1: empty"
		return
	fi
	gh variable set "$1" --repo "$repo" --body "$2" >/dev/null
	echo "set   variable $1"
}

set_secret() {
	if [[ -z "$2" ]]; then
		echo "skip  secret $1 on ${env_name}: no Terraform output"
		return
	fi
	printf '%s' "$2" | gh secret set "$1" --env "$env_name" --repo "$repo"
	echo "set   secret $1 on ${env_name}"
}

# --- Repository variables: non-secret, visible in logs --------------------------------------
set_var AWS_REGION "${AWS_REGION:-}"
set_var NAME_SUFFIX "${NAME_SUFFIX:-}"
set_var CLUSTER_NAME "$(out cluster_name)"
set_var TRAIL_NAME "$(out trail_name)"
set_var EXPOSURE "${EXPOSURE:-}"
set_var DOMAIN "${DOMAIN:-}"
set_var ALLOWED_CIDRS "${ALLOWED_CIDRS:-}"
set_var WATCHED_IAM_PATHS "${WATCHED_IAM_PATHS:-}"
set_var TRIAGE_MODEL "${TRIAGE_MODEL:-}"
set_var TRIAGE_MODEL_ESCALATION "${TRIAGE_MODEL_ESCALATION:-}"
set_var GRAFANA_URL "${GRAFANA_URL:-}"
set_var DEMO_BUCKET "${DEMO_BUCKET:-}"

# --- consoletrack-deploy environment secrets: contain or point at the account ---------------
set_secret CHANGES_QUEUE_URL "$(out changes_queue_url)"
set_secret CHANGES_DLQ_URL "$(out changes_dlq_url)"
set_secret CANARY_SECURITY_GROUP_ID "$(out canary_security_group_id)"
if [[ -n "${DOMAIN:-}" ]]; then
	set_secret CERTIFICATE_ARN "$(out certificate_arn)"
else
	echo "skip  secret CERTIFICATE_ARN on ${env_name}: DOMAIN is empty"
fi

echo "Done. AWS_ROLE_ARN is set separately: make gh-secrets"
