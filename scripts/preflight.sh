#!/usr/bin/env bash
# `make preflight`: the checks that fail cheaply, before `make up` spends 20 minutes on Terraform.
# Tools on PATH, the AWS identity (account ID masked), ANTHROPIC_API_KEY set, the helm-diff plugin,
# EXPOSURE=alb's inputs, and the images at IMAGE_TAG in GHCR. Read-only; never prints a secret or the account ID.
#
# Not here yet (#83): Spot quota, the Kubernetes version in your region, and the CAA records check.
set -euo pipefail

fail=0
problem() {
	echo "preflight: $*" >&2
	fail=1
}

for tool in terraform aws kubectl helm helmfile jq; do
	if command -v "$tool" >/dev/null; then
		echo "preflight: $tool found"
	else
		problem "$tool is not on PATH (README → Prerequisites)"
	fi
done

if command -v helm >/dev/null && ! helm plugin list 2>/dev/null | grep -qE '^diff[[:space:]]'; then
	problem "the helm-diff plugin is missing (helmfile apply needs it): helm plugin install https://github.com/databus23/helm-diff"
fi

if command -v aws >/dev/null; then
	if arn=$(aws sts get-caller-identity --query Arn --output text 2>/dev/null); then
		echo "preflight: AWS identity $(sed -E 's/[0-9]{12}/***/g' <<<"$arn")"
	else
		problem "no AWS identity for AWS_PROFILE=${AWS_PROFILE:-<unset>} (aws sso login --profile ...)"
	fi
fi

[[ -n "${ANTHROPIC_API_KEY:-}" && "${ANTHROPIC_API_KEY}" != op://* ]] || problem "ANTHROPIC_API_KEY is not set (export it, or op run --env-file=.env -- make ...)"
[[ -n "${NAME_SUFFIX:-}" ]] || problem "NAME_SUFFIX is empty; run make bootstrap and paste the suffix into .env"
if [[ "${EXPOSURE:-}" == alb ]]; then
	[[ -n "${DOMAIN:-}" ]] || problem "EXPOSURE=alb needs DOMAIN"
	[[ -n "${ALLOWED_CIDRS:-}" ]] || problem "EXPOSURE=alb needs ALLOWED_CIDRS"
fi

# The tag `make deploy` rolls out must exist in GHCR, or every pod sits in ImagePullBackOff. The
# images are public, so this needs no login; skipped where Docker isn't installed.
if [[ -n "${IMAGE_TAG:-}" ]] && command -v docker >/dev/null; then
	owner=${GHCR_OWNER:-jennasrunbooks}
	for name in backend frontend k8s-mcp demo-app; do
		ref="ghcr.io/$owner/consoletrack-$name:$IMAGE_TAG"
		if docker manifest inspect "$ref" >/dev/null 2>&1; then
			echo "preflight: $ref found"
		else
			problem "$ref doesn't exist (or is private): run make images at this commit, or set IMAGE_TAG to a pushed tag"
		fi
	done
fi

if ((fail)); then
	echo "preflight: failed" >&2
	exit 1
fi
echo "preflight: ok"
