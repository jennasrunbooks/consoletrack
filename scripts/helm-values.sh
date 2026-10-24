#!/usr/bin/env bash
# Writes helm/environments/values.generated.yaml (gitignored, mode 0600) from .env and the
# Terraform outputs, with the key names of helm/tests/values/eks.yaml. Run by `make helm-values`,
# which `make addons`, `make deploy`, and `make down` depend on.
#
# Never holds a secret. The queue URLs and the certificate ARN contain the account ID, which is
# why the file stays out of the repo; nothing here prints a value.
#
# HELM_VALUES_SOURCE picks where the Terraform outputs come from:
#   terraform  (default) `terraform output -json` of consoletrack/us-east-1, and of
#              demo-app/us-east-1 when it has .tf files and state
#   env        environment variables, for deploy.yaml, whose role can't read Terraform state:
#              CLUSTER_NAME, CHANGES_QUEUE_URL, CHANGES_DLQ_URL, TRAIL_NAME,
#              CANARY_SECURITY_GROUP_ID (required); VPC_ID, CERTIFICATE_ARN, DEMO_BUCKET (optional)
#   none       only the .env keys, for `make down` when the outputs are already gone
# For tests, HELM_VALUES_REGION_OUTPUTS and HELM_VALUES_DEMO_OUTPUTS name JSON files that stand
# in for the two `terraform output -json` calls.
#
# Reads the .env settings from the environment (the Makefile exports them): EXPOSURE, DOMAIN,
# ALLOWED_CIDRS, NAME_SUFFIX, AWS_REGION, WATCHED_IAM_PATHS, TRIAGE_MODEL,
# TRIAGE_MODEL_ESCALATION, GRAFANA_URL. An empty one is left out, so the default in
# helm/environments/eks.yaml applies.
set -euo pipefail

die() {
	echo "helm-values: $*" >&2
	exit 1
}

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
out=${HELM_VALUES_OUT:-$root/helm/environments/values.generated.yaml}
source=${HELM_VALUES_SOURCE:-terraform}
terraform=${TERRAFORM:-terraform}
command -v jq >/dev/null || die "needs jq"

lines=()
# add <key> <value>: a string key. JSON strings are valid YAML, so @json quotes safely.
add() {
	[[ -n "${2:-}" ]] || return 0
	lines+=("$1: $(jq -nr --arg v "$2" '$v|@json')")
}

# --- .env ---------------------------------------------------------------------------------------
add exposure "${EXPOSURE:-}"
add domain "${DOMAIN:-}"
add nameSuffix "${NAME_SUFFIX:-}"
add awsRegion "${AWS_REGION:-}"
add watchedIamPaths "${WATCHED_IAM_PATHS:-}"
add triageModel "${TRIAGE_MODEL:-}"
add triageModelEscalation "${TRIAGE_MODEL_ESCALATION:-}"
add grafanaUrl "${GRAFANA_URL:-}"
if [[ -n "${ALLOWED_CIDRS:-}" ]]; then
	cidrs=$(jq -nc --arg v "$ALLOWED_CIDRS" '$v | split(",") | map(gsub("^\\s+|\\s+$"; "")) | map(select(. != ""))')
	lines+=("allowedCidrs: $cidrs")
fi
if [[ "${EXPOSURE:-}" == alb && -z "${ALLOWED_CIDRS:-}" ]]; then
	die "EXPOSURE=alb needs ALLOWED_CIDRS (the CIDRs that may reach the ALB); set it in .env"
fi

# --- Terraform outputs --------------------------------------------------------------------------
# tf_output <json> <name>: the output's value, or empty when it is absent or null.
tf_output() {
	jq -r --arg n "$2" '(.[$n].value // empty) | tostring' <<<"$1"
}

region_json=
demo_json=
case "$source" in
terraform)
	if [[ -n "${HELM_VALUES_REGION_OUTPUTS:-}" ]]; then
		region_json=$(<"$HELM_VALUES_REGION_OUTPUTS")
	else
		region_json=$("$terraform" -chdir="$root/terraform/consoletrack/us-east-1" output -json) ||
			die "no Terraform outputs from consoletrack/us-east-1; run make up first (or make tf-init ROOT=consoletrack/us-east-1)"
	fi
	if [[ -n "${HELM_VALUES_DEMO_OUTPUTS:-}" ]]; then
		demo_json=$(<"$HELM_VALUES_DEMO_OUTPUTS")
	elif compgen -G "$root/terraform/demo-app/us-east-1/*.tf" >/dev/null; then
		# No state yet (or no bucket output) = no bucket: the demo app then reads no S3 object.
		demo_json=$("$terraform" -chdir="$root/terraform/demo-app/us-east-1" output -json 2>/dev/null) || demo_json=
	fi
	add clusterName "$(tf_output "$region_json" cluster_name)"
	add vpcId "$(tf_output "$region_json" vpc_id)"
	add certificateArn "$(tf_output "$region_json" certificate_arn)"
	add changesQueueUrl "$(tf_output "$region_json" changes_queue_url)"
	add changesDlqUrl "$(tf_output "$region_json" changes_dlq_url)"
	add trailName "$(tf_output "$region_json" trail_name)"
	add canarySecurityGroupId "$(tf_output "$region_json" canary_security_group_id)"
	if [[ -n "$demo_json" ]]; then
		add demoBucket "$(tf_output "$demo_json" bucket_name)"
	fi
	;;
env)
	for v in CLUSTER_NAME CHANGES_QUEUE_URL CHANGES_DLQ_URL TRAIL_NAME CANARY_SECURITY_GROUP_ID; do
		[[ -n "${!v:-}" ]] || die "HELM_VALUES_SOURCE=env needs $v"
	done
	add clusterName "$CLUSTER_NAME"
	add vpcId "${VPC_ID:-}"
	add certificateArn "${CERTIFICATE_ARN:-}"
	add changesQueueUrl "$CHANGES_QUEUE_URL"
	add changesDlqUrl "$CHANGES_DLQ_URL"
	add trailName "$TRAIL_NAME"
	add canarySecurityGroupId "$CANARY_SECURITY_GROUP_ID"
	add demoBucket "${DEMO_BUCKET:-}"
	;;
none) ;;
*) die "HELM_VALUES_SOURCE must be terraform, env, or none, not $source" ;;
esac

umask 077
mkdir -p "$(dirname "$out")"
{
	echo "# Written by scripts/helm-values.sh (make helm-values) from .env and the Terraform outputs."
	echo "# Gitignored and never a secret, but it holds the account ID (queue URLs): don't commit it."
	printf '%s\n' ${lines[@]+"${lines[@]}"}
} >"$out"
echo "helm-values: wrote ${out#"$root"/} ($((${#lines[@]})) keys, source: $source)"
