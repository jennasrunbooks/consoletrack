#!/usr/bin/env bash
# Creates the Secrets the consoletrack chart references by name, in the consoletrack namespace,
# each only if it is missing (so a rerun never rotates anything):
#
#   consoletrack-api-key  anthropic-api-key
#   consoletrack-auth     admin-password-hash, viewer-password-hash, session-secret
#   consoletrack-webhook  token
#
# Run by a human through `make create-secrets` (also part of `make up`). Values travel only on
# stdin, as `kubectl create secret --from-env-file=/dev/stdin --dry-run=client -o yaml |
# kubectl apply -f -`: never on a command line, in a file, in Terraform, or in Helm values.
#
# Reads from the environment (the Makefile exports .env; your shell wins):
#   ANTHROPIC_API_KEY     required for a new consoletrack-api-key
#   ADMIN_PASSWORD_HASH   optional: use your own admin password. Otherwise a password is
#                         generated and printed once, on first creation only.
#   VIEWER_PASSWORD_HASH  optional; empty means the viewer can't log in (.env.example)
#   KUBE_CONTEXT          the kubectl context; never the current one (default consoletrack-eks)
#   CREATE_SECRETS_HASH   command that hashes the password on stdin (default: make hash-password)
set -euo pipefail

die() {
	echo "create-secrets: $*" >&2
	exit 1
}

namespace=consoletrack
context=${KUBE_CONTEXT:-consoletrack-eks}
kubectl=(kubectl --context "$context")
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

for tool in kubectl openssl; do
	command -v "$tool" >/dev/null || die "needs $tool"
done

# Namespaced Secrets need the namespace before the consoletrack release creates it (its
# createNamespace tolerates one that already exists).
"${kubectl[@]}" create namespace "$namespace" --dry-run=client -o yaml | "${kubectl[@]}" apply -f - >/dev/null

exists() {
	"${kubectl[@]}" -n "$namespace" get secret "$1" -o name >/dev/null 2>&1
}

# apply_secret <name>: key=value lines on stdin become the Secret's data.
apply_secret() {
	"${kubectl[@]}" -n "$namespace" create secret generic "$1" --from-env-file=/dev/stdin \
		--dry-run=client -o yaml | "${kubectl[@]}" apply -f - >/dev/null
}

random() {
	openssl rand -hex "$1"
}

hash_password() {
	if [[ -n "${CREATE_SECRETS_HASH:-}" ]]; then
		# shellcheck disable=SC2086 # a command line, split on purpose
		$CREATE_SECRETS_HASH
	else
		make -s -C "$root" --no-print-directory hash-password HASH_ARGS=--stdin
	fi
}

# --- consoletrack-api-key -----------------------------------------------------------------------
if exists consoletrack-api-key; then
	echo "create-secrets: consoletrack-api-key exists, left as is"
else
	[[ -n "${ANTHROPIC_API_KEY:-}" ]] || die "ANTHROPIC_API_KEY is not set (export it in your shell; it is never read from a file)"
	printf 'anthropic-api-key=%s\n' "$ANTHROPIC_API_KEY" | apply_secret consoletrack-api-key
	echo "create-secrets: created consoletrack-api-key"
fi

# The prefix of an argon2id hash, with its literal $ signs.
# shellcheck disable=SC2016
argon='$argon2id$'

# --- consoletrack-auth --------------------------------------------------------------------------
admin_password=
if exists consoletrack-auth; then
	echo "create-secrets: consoletrack-auth exists, left as is"
else
	admin_hash=${ADMIN_PASSWORD_HASH:-}
	if [[ -z "$admin_hash" ]]; then
		admin_password=$(random 12)
		admin_hash=$(printf '%s\n' "$admin_password" | hash_password) || die "could not hash the admin password"
	fi
	# `make hash-password` is the one place passwords are hashed, so the backend verifies what it made.
	[[ "$admin_hash" == "$argon"* ]] || die "ADMIN_PASSWORD_HASH is not an argon2id hash (make hash-password)"
	viewer_hash=${VIEWER_PASSWORD_HASH:-}
	if [[ -n "$viewer_hash" && "$viewer_hash" != "$argon"* ]]; then
		die "VIEWER_PASSWORD_HASH is not an argon2id hash (make hash-password)"
	fi
	printf 'admin-password-hash=%s\nviewer-password-hash=%s\nsession-secret=%s\n' \
		"$admin_hash" "$viewer_hash" "$(random 32)" | apply_secret consoletrack-auth
	echo "create-secrets: created consoletrack-auth"
fi

# --- consoletrack-webhook -----------------------------------------------------------------------
if exists consoletrack-webhook; then
	echo "create-secrets: consoletrack-webhook exists, left as is"
else
	printf 'token=%s\n' "$(random 32)" | apply_secret consoletrack-webhook
	echo "create-secrets: created consoletrack-webhook"
fi

# --- the one-time password ----------------------------------------------------------------------
if [[ -n "$admin_password" ]]; then
	echo
	echo "create-secrets: the admin password is shown once and stored only as a hash:"
	echo
	echo "  admin / $admin_password"
	echo
	echo "To choose your own, set ADMIN_PASSWORD_HASH (make hash-password) before the first run,"
	echo "or delete the consoletrack-auth Secret and run make create-secrets again."
fi
