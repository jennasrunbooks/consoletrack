#!/usr/bin/env bash
# Store each GitHub OIDC role ARN as its environment's AWS_ROLE_ARN secret, from the
# github_env_role_arns output of terraform/consoletrack/global. Run it through `make gh-secrets`
# after the first apply of that root, and again whenever NAME_SUFFIX changes (the role names
# include it). Safe to re-run: it overwrites the four secrets with the current values.
#
# Needs GITHUB_REPO (owner/repo), an initialized global root (`make tf-init
# ROOT=consoletrack/global`), and `gh` logged in with permission to set environment secrets.
# The ARNs contain the account ID, so they go to `gh` on stdin and are never printed.
set -euo pipefail

ROOT_DIR=terraform/consoletrack/global
ENVIRONMENTS=(consoletrack-plan consoletrack-apply consoletrack-revert consoletrack-deploy)

die() {
	echo "gh-env-secrets: $*" >&2
	exit 1
}

[[ -n "${GITHUB_REPO:-}" ]] || die "GITHUB_REPO is not set (owner/repo, in .env or your shell)."
for tool in terraform gh jq; do
	type -P "$tool" >/dev/null || die "$tool is required."
done
gh auth status >/dev/null 2>&1 || die "gh is not logged in; run \`gh auth login\`."

arns=$(terraform -chdir="$ROOT_DIR" output -json github_env_role_arns 2>/dev/null) ||
	die "no github_env_role_arns output; run \`make tf-init ROOT=consoletrack/global\` and apply the root first."
[[ "$(jq 'length' <<<"$arns")" -gt 0 ]] ||
	die "github_env_role_arns is empty: set ENABLE_GITHUB_OIDC=true and apply terraform/consoletrack/global."

for env in "${ENVIRONMENTS[@]}"; do
	arn=$(jq -r --arg e "$env" '.[$e] // empty' <<<"$arns")
	[[ -n "$arn" ]] || die "github_env_role_arns has no entry for $env."
	gh secret set AWS_ROLE_ARN --env "$env" --repo "$GITHUB_REPO" <<<"$arn" >/dev/null ||
		die "could not set AWS_ROLE_ARN in environment $env; does the environment exist in $GITHUB_REPO?"
	echo "Set AWS_ROLE_ARN in environment $env."
done
