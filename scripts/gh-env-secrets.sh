#!/usr/bin/env bash
# Sets the AWS_ROLE_ARN secret on each GitHub environment from Terraform outputs.
#
# Run on your own machine, as yourself, after applying terraform/<account>/global:
#   scripts/gh-env-secrets.sh [account]      # default account: consoletrack
#
# Reads the `github_env_role_arns` output (a map of environment name -> role ARN),
# pipes each ARN into `gh secret set` over stdin, and never prints an ARN or account ID.
# Safe to rerun: it overwrites each secret with the current value.
set -euo pipefail

account="${1:-consoletrack}"
root="terraform/${account}/global"
repo="${GITHUB_REPO:?set GITHUB_REPO (owner/repo) in .env or your shell}"

for tool in gh jq terraform; do
  command -v "$tool" >/dev/null || { echo "gh-env-secrets: needs $tool" >&2; exit 1; }
done

# Refuse to run as the coding agent's machine user (e.g. a leaked GH_TOKEN).
login=$(gh api user --jq .login)
if [[ "$login" == *-agent ]]; then
  echo "gh-env-secrets: gh is authenticated as ${login}; unset GH_TOKEN and run this as yourself." >&2
  exit 1
fi

if ! arns=$(terraform -chdir="$root" output -json github_env_role_arns 2>/dev/null); then
  echo "gh-env-secrets: no github_env_role_arns output in ${root}; apply it first (with ENABLE_GITHUB_OIDC=true)." >&2
  exit 1
fi

if [[ $(jq 'length' <<<"$arns") -eq 0 ]]; then
  echo "gh-env-secrets: github_env_role_arns is empty; is ENABLE_GITHUB_OIDC=true?" >&2
  exit 1
fi

existing=$(gh api "repos/${repo}/environments" --jq '.environments[].name')
missing=0

while read -r env; do
  if ! grep -qx -- "$env" <<<"$existing"; then
    echo "skip  ${env}: environment doesn't exist in ${repo}; create it first" >&2
    missing=1
    continue
  fi
  jq -r --arg e "$env" '.[$e]' <<<"$arns" | gh secret set AWS_ROLE_ARN --env "$env" --repo "$repo"
  echo "set   AWS_ROLE_ARN on ${env}"
done < <(jq -r 'keys[]' <<<"$arns")

exit "$missing"
