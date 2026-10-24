#!/usr/bin/env bash
# Removes the network interfaces a destroyed EKS node group leaves in the project VPC, which
# otherwise block deleting the node security group and the subnets ("DependencyViolation").
#
# The VPC CNI (aws-node) creates an ENI per pod IP range. It releases them as the node shuts down,
# using its Pod Identity role; if that role is gone first, or the node is reclaimed abruptly, the
# ENI is left `available` with the description "aws-K8S-i-<instance>". `make down` destroys the
# node group first and runs this before the rest of the root; `make clean-enis` runs it by hand.
#
# Only touches ENIs in a VPC tagged project=consoletrack, and only:
#   - deletes `available` ones described aws-K8S-* (the CNI's)
#   - for ones described "Amazon EKS <cluster>" (the control plane's cross-account ENIs):
#       - cluster still running: leaves them alone. They're attached from an AWS-owned account, so
#         they can't be detached from ours, and EKS removes them when Terraform deletes the
#         cluster in the next step of `make down`. Waiting for them here would never end.
#       - cluster deleting or gone: waits for AWS to release them, and deletes any left `available`
# Everything else, including the NAT gateway's ENI, belongs to Terraform and is left alone, and an
# in-use ENI is never deleted.
#
# CLEAN_ENIS_WAIT=<seconds> waits that long for EKS-managed ENIs (default 0: don't wait; make
# down passes 600). CLEAN_ENIS_ASSUME_YES=1 skips the confirmation. Reads AWS_REGION and
# AWS_PROFILE; prints ENI IDs and descriptions only, never an account ID.
set -euo pipefail

die() {
	echo "delete-leftover-enis: $*" >&2
	exit 1
}

region=${AWS_REGION:?set AWS_REGION}
wait_seconds=${CLEAN_ENIS_WAIT:-0}
aws() { command aws --region "$region" --no-cli-pager "$@"; }

vpcs=$(aws ec2 describe-vpcs --filters Name=tag:project,Values=consoletrack \
	--query 'Vpcs[].VpcId' --output text | tr '\t' ',')
if [[ -z "$vpcs" ]]; then
	echo "delete-leftover-enis: no project=consoletrack VPC in $region; nothing to clean"
	exit 0
fi

# The cluster's status (ACTIVE, DELETING, ...), or "gone" when EKS has no such cluster.
cluster_status() {
	local out
	if out=$(aws eks describe-cluster --name "$1" --query cluster.status --output text 2>&1); then
		echo "$out"
	elif [[ "$out" == *ResourceNotFoundException* ]]; then
		echo gone
	else
		die "can't tell whether cluster $1 still exists: $(sed -E 's/[0-9]{12}/***/g' <<<"$out")"
	fi
}

# Rows of "<eni>\t<status>\t<description>" for the project VPCs.
list() {
	aws ec2 describe-network-interfaces --filters "Name=vpc-id,Values=$vpcs" \
		--query 'NetworkInterfaces[].[NetworkInterfaceId,Status,Description]' --output text
}

confirmed=${CLEAN_ENIS_ASSUME_YES:-}
deadline=$((SECONDS + wait_seconds))
deleted=0
while :; do
	rows=$(list)
	leftovers=$(awk -F'\t' '$2 == "available" && $3 ~ /^aws-K8S-/ { print $1 "\t" $3 }' <<<"$rows")
	managed="" live=""
	while IFS=$'\t' read -r eni status desc; do
		[[ -n "$eni" ]] || continue
		cluster=$(cluster_status "${desc#Amazon EKS }") || exit 1
		case $cluster in
		gone | DELETING)
			if [[ "$status" == available ]]; then
				leftovers+=${leftovers:+$'\n'}"$eni"$'\t'"$desc"
			else
				managed+=${managed:+$'\n'}"$eni"$'\t'"$desc"
			fi
			;;
		*) live+=${live:+$'\n'}"$eni"$'\t'"$desc" ;;
		esac
	done < <(awk -F'\t' '$3 ~ /^Amazon EKS / { print $1 "\t" $2 "\t" $3 }' <<<"$rows")

	if [[ -n "$leftovers" ]]; then
		if [[ -z "$confirmed" ]]; then
			echo "Leftover EKS network interfaces (available, so nothing uses them):"
			# shellcheck disable=SC2001 # indents every line
			sed 's/^/  /' <<<"$leftovers" | sed -E 's/[0-9]{12}/***/g'
			[[ -t 0 ]] || die "run from an interactive terminal, or set CLEAN_ENIS_ASSUME_YES=1"
			read -r -p "Delete them? [y/N] " reply
			[[ "$reply" =~ ^[Yy]$ ]] || die "aborted; nothing deleted"
			confirmed=1
		fi
		while IFS=$'\t' read -r eni _; do
			echo "delete-leftover-enis: deleting $eni"
			aws ec2 delete-network-interface --network-interface-id "$eni"
			deleted=$((deleted + 1))
		done <<<"$leftovers"
		continue
	fi

	if [[ -z "$managed" ]]; then
		if [[ -n "$live" ]]; then
			echo "delete-leftover-enis: left $(wc -l <<<"$live" | tr -d ' ') control-plane network interface(s) of a running cluster; EKS removes them when the cluster is deleted"
		fi
		echo "delete-leftover-enis: no leftover EKS network interfaces in the project VPC (deleted $deleted)"
		exit 0
	fi
	if ((SECONDS >= deadline)); then
		echo "delete-leftover-enis: still in use after ${wait_seconds}s; AWS releases these when the cluster is gone:" >&2
		# shellcheck disable=SC2001 # indents every line
		sed 's/^/  /' <<<"$managed" | sed -E 's/[0-9]{12}/***/g' >&2
		exit 1
	fi
	echo "delete-leftover-enis: waiting for $(wc -l <<<"$managed" | tr -d ' ') EKS-managed network interface(s) to be released..."
	sleep 15
done
